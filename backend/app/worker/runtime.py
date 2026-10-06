"""Bucle del worker. El modelo y el índice se abren una vez por proceso."""

import asyncio
import contextlib
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import Settings, resolve_file_store_path, resolve_model_cache_dir
from app.documents.chunking import TokenCounter
from app.documents.orphans import plan_orphan_deletions
from app.documents.store import FileStore
from app.domain.errors import ConflictError, NotFoundError
from app.embeddings.base import EmbeddingProvider
from app.infrastructure.database import create_database_engine, create_session_factory
from app.infrastructure.local_files import LocalFileStore
from app.infrastructure.qdrant_store import QdrantVectorStore
from app.infrastructure.vector_client import create_qdrant_client
from app.ingestion.context import context_for_job
from app.ingestion.failures import LeaseLost, StopAfterUpsert, backoff_seconds, classify_failure
from app.ingestion.pipeline import Clock, DocumentPipeline, PipelineHooks
from app.ingestion.queue import ClaimedJob
from app.persistence.job_queue import SqlAlchemyIngestionQueue
from app.persistence.repositories.documents import SqlAlchemyDocumentRepository
from app.persistence.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)


def note_liveness() -> None:
    """Actualiza el pulso del worker. Sin la variable, el proceso local no escribe nada."""
    raw = os.environ.get("RAG_WORKER_HEARTBEAT_PATH", "").strip()
    if not raw:
        return
    path = Path(raw)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok", encoding="ascii")
    except OSError:
        logger.info("pulso status=error")


async def _pulse(stop: asyncio.Event) -> None:
    """Mantiene el pulso fresco mientras el modelo se descarga o el bucle espera."""
    while not stop.is_set():
        note_liveness()
        try:
            await asyncio.wait_for(stop.wait(), timeout=15)
        except TimeoutError:
            continue


_QDRANT_TIMEOUT_SECONDS = 30


class SystemClock:
    """Reloj UTC del proceso."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class IngestionWorker:
    """Reclama de a un trabajo y no abre otra transacción durante el embedido."""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        qdrant: AsyncQdrantClient,
        session_factory: async_sessionmaker[AsyncSession],
        queue: SqlAlchemyIngestionQueue,
        pipeline: DocumentPipeline,
        vectors: QdrantVectorStore,
        file_store: FileStore,
        owner: str,
        poll_seconds: float,
        lease: timedelta,
        heartbeat_seconds: float,
        backoff_base_seconds: float,
        backoff_cap_seconds: float,
        clock: Clock,
        pseudonym_secret: str,
        audit_retention_days: int,
        conversation_retention_days: int = 30,
        retention_batch_size: int = 100,
        retention_interval_hours: int = 24,
    ) -> None:
        self._engine = engine
        self._qdrant = qdrant
        self._session_factory = session_factory
        self._queue = queue
        self._pipeline = pipeline
        self._vectors = vectors
        self._files = file_store
        self._owner = owner
        self._poll_seconds = poll_seconds
        self._lease = lease
        self._heartbeat_seconds = heartbeat_seconds
        self._backoff_base_seconds = backoff_base_seconds
        self._backoff_cap_seconds = backoff_cap_seconds
        self._clock = clock
        self._pseudonym_secret = pseudonym_secret
        self._audit_retention_days = audit_retention_days
        self._conversation_retention_days = conversation_retention_days
        self._retention_batch = retention_batch_size
        self._retention_interval = timedelta(hours=retention_interval_hours)
        self._retention_after = datetime.min.replace(tzinfo=UTC)

    async def run(self, stop: asyncio.Event) -> None:
        """Reclama hasta que `stop` queda marcado. El trabajo en curso termina."""
        pulse = asyncio.create_task(_pulse(stop))
        try:
            await self._prepare()
            while not stop.is_set():
                if await self._once():
                    continue
                await self._maybe_retain()
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self._poll_seconds)
                except TimeoutError:
                    continue
        finally:
            pulse.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pulse

    async def run_available(self) -> int:
        """Cierra lo ya consistente y procesa lo reclamable. No espera al poll."""
        await self._prepare()
        processed = 0
        while await self._once():
            processed += 1
        return processed

    async def aclose(self) -> None:
        """Cierra el cliente de Qdrant y el engine de PostgreSQL."""
        await self._qdrant.close()
        await self._engine.dispose()

    async def _prepare(self) -> None:
        await self._vectors.ensure_index()
        closed = await self._queue.finalize_consistent(self._clock.now())
        if closed:
            logger.info("reconciliados=%d", closed)
        removed = await self._sweep_orphan_files()
        if removed:
            logger.info("huérfanos eliminados=%d", removed)
        await self._maybe_retain()
        await self._publish_queue()

    async def _maybe_retain(self) -> None:
        now = self._clock.now()
        if now < self._retention_after:
            return
        root = getattr(self._files, "_root", None)
        if not isinstance(root, Path):
            return
        try:
            from app.infrastructure.export_files import ExportFileStore
            from app.retention.service import RetentionService

            outcome = await RetentionService(
                self._session_factory,
                ExportFileStore(root),
                conversation_days=self._conversation_retention_days,
                audit_days=self._audit_retention_days,
                batch_size=self._retention_batch,
                clock=self._clock,
            ).purge_once(dry_run=False)
        except ConflictError:
            self._retention_after = now + timedelta(minutes=5)
            return
        except Exception:
            logger.info("retencion status=error")
            self._retention_after = now + timedelta(minutes=5)
            return
        self._retention_after = now if outcome.more else now + self._retention_interval
        logger.info(
            "retencion conversations=%d traces=%d audit=%d exports=%d",
            outcome.conversations,
            outcome.traces,
            outcome.audit,
            outcome.exports,
        )

    async def _publish_queue(self) -> None:
        from sqlalchemy import text

        from app.infrastructure.metrics import set_queue_counts

        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    text("SELECT status, count(*) FROM ingestion_jobs GROUP BY status")
                )
                counts = {str(status): int(count) for status, count in result.all()}
            set_queue_counts(counts)
        except Exception:
            logger.info("cola metricas status=error")

    async def _sweep_orphan_files(self) -> int:
        """Borra objetos locales sin fila en `document_versions` fuera del periodo de gracia."""
        if not isinstance(self._files, LocalFileStore):
            return 0
        referenced = await self._queue.list_referenced_storage_keys()
        stored = await asyncio.to_thread(self._files.list_stored_mtimes)
        staged = await asyncio.to_thread(self._files.list_staged_mtimes)
        plan = plan_orphan_deletions(
            stored_mtimes=stored,
            staged_mtimes=staged,
            referenced_keys=referenced,
            now=self._clock.now(),
        )
        for key in plan.storage_keys:
            await asyncio.to_thread(self._files.delete, key)
        for name in plan.staged_names:
            await asyncio.to_thread(self._files.discard_staged_name, name)
        return plan.total

    async def _once(self) -> bool:
        job = await self._queue.claim_next(self._owner, self._clock.now(), self._lease)
        if job is None:
            return False
        logger.info(
            "trabajo reclamado job=%s document=%s type=%s",
            job.id,
            job.document_id,
            job.job_type.value,
        )
        from app.observability.context import bind_job, reset_job
        from app.observability.pseudonym import pseudonym

        tokens = bind_job(
            job_id=job.id,
            origin_request_id=job.origin_request_id,
            tenant_ref=pseudonym(self._pseudonym_secret, "tenant", str(job.tenant_id)),
        )
        try:
            await self._process(job)
        finally:
            reset_job(tokens)
            await self._publish_queue()
        return True

    async def _process(self, job: ClaimedJob) -> None:
        lost = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(job, lost))
        from app.infrastructure.metrics import observe_job

        try:
            await self._pipeline.run(job, lease_lost=lost)
        except LeaseLost:
            logger.info(
                "lease perdido job=%s document=%s",
                job.id,
                job.document_id,
            )
            observe_job(job.job_type.value, "lease_lost")
        except StopAfterUpsert:
            logger.info(
                "trabajo interrumpido job=%s document=%s stage=upsert",
                job.id,
                job.document_id,
            )
            observe_job(job.job_type.value, "interrupted")
        except Exception as error:
            await self._fail(job, error)
        else:
            observe_job(job.job_type.value, "succeeded")
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _heartbeat(self, job: ClaimedJob, lost: asyncio.Event) -> None:
        while not lost.is_set():
            note_liveness()
            try:
                await asyncio.wait_for(lost.wait(), timeout=self._heartbeat_seconds)
            except TimeoutError:
                try:
                    renewed = await self._queue.heartbeat(
                        job.id,
                        job.lease_owner,
                        self._clock.now(),
                        self._lease,
                    )
                except Exception:
                    logger.info(
                        "heartbeat fallido job=%s document=%s",
                        job.id,
                        job.document_id,
                    )
                    lost.set()
                    return
                if not renewed:
                    lost.set()
                continue
            return

    async def _fail(self, job: ClaimedJob, error: Exception) -> None:
        failure = classify_failure(error)
        logger.info(
            "trabajo fallido job=%s document=%s code=%s permanent=%s error=%s",
            job.id,
            job.document_id,
            failure.code,
            failure.permanent,
            type(error).__name__,
        )
        from app.infrastructure.metrics import observe_job

        observe_job(job.job_type.value, "dead" if failure.permanent else "failed")
        now = self._clock.now()
        if not await self._queue.still_owned(job.id, job.lease_owner, now):
            return
        if failure.permanent:
            await self._mark_failed(job, failure.code)
            await self._queue.fail_permanent(
                job.id,
                job.lease_owner,
                now=self._clock.now(),
                error_code=failure.code,
            )
            return
        delay = backoff_seconds(
            attempts=min(job.attempts + 1, job.max_attempts),
            base_seconds=self._backoff_base_seconds,
            cap_seconds=self._backoff_cap_seconds,
        )
        available_at = self._clock.now() + timedelta(seconds=delay)
        result = await self._queue.retry(
            job.id,
            job.lease_owner,
            now=self._clock.now(),
            available_at=available_at,
            error_code=failure.code,
        )
        if result.dead:
            await self._mark_failed(job, failure.code)

    async def _mark_failed(self, job: ClaimedJob, error_code: str) -> None:
        try:
            async with UnitOfWork(self._session_factory) as unit:
                repository = SqlAlchemyDocumentRepository(unit, context_for_job(job.tenant_id))
                await repository.mark_failed(
                    document_id=job.document_id,
                    version_id=job.document_version_id,
                    error_code=error_code,
                    updated_at=self._clock.now(),
                )
                await unit.commit()
        except NotFoundError:
            return


def build_worker(
    settings: Settings,
    *,
    embeddings: EmbeddingProvider | None = None,
    counter: TokenCounter | None = None,
    file_store: FileStore | None = None,
    database_url: str | None = None,
    qdrant_url: str | None = None,
    index_revision: str | None = None,
    vector_alias: str | None = None,
    hooks: PipelineHooks | None = None,
    owner: str | None = None,
    clock: Clock | None = None,
) -> IngestionWorker:
    """Compone cola, pipeline y proveedores. El modelo real solo se carga si no hay fakes."""
    provider = embeddings if embeddings is not None else _production_embeddings(settings)
    token_counter = counter if counter is not None else _production_counter(settings)
    store = (
        file_store if file_store is not None else LocalFileStore(resolve_file_store_path(settings))
    )
    engine = create_database_engine(database_url or settings.database_url)
    session_factory = create_session_factory(engine)
    client = create_qdrant_client(
        qdrant_url or settings.qdrant_url,
        timeout_seconds=_QDRANT_TIMEOUT_SECONDS,
    )
    vectors = QdrantVectorStore(
        client,
        embedding_model=provider.model_version,
        revision=index_revision or settings.index_revision,
        dimension=provider.dimension,
        alias_name=vector_alias or settings.vector_alias,
        batch_size=settings.embed_batch_size,
    )
    queue = SqlAlchemyIngestionQueue(session_factory)
    resolved_clock = clock if clock is not None else SystemClock()
    pipeline = DocumentPipeline(
        session_factory,
        store,
        vectors,
        provider,
        token_counter,
        queue,
        embed_batch_size=settings.embed_batch_size,
        clock=resolved_clock,
        hooks=hooks,
    )
    return IngestionWorker(
        engine=engine,
        qdrant=client,
        session_factory=session_factory,
        queue=queue,
        pipeline=pipeline,
        vectors=vectors,
        file_store=store,
        owner=owner if owner is not None else str(uuid4()),
        poll_seconds=settings.worker_poll_seconds,
        lease=timedelta(seconds=settings.worker_lease_seconds),
        heartbeat_seconds=float(settings.worker_heartbeat_seconds),
        backoff_base_seconds=settings.worker_backoff_base_seconds,
        backoff_cap_seconds=settings.worker_backoff_cap_seconds,
        clock=resolved_clock,
        pseudonym_secret=settings.pseudonym_secret,
        audit_retention_days=settings.audit_retention_days,
        conversation_retention_days=settings.conversation_retention_days,
        retention_batch_size=settings.retention_batch_size,
        retention_interval_hours=settings.retention_interval_hours,
    )


def _production_embeddings(settings: Settings) -> EmbeddingProvider:
    from app.embeddings.sentence_transformer import SentenceTransformerEmbeddingProvider

    return SentenceTransformerEmbeddingProvider(
        settings.embedding_model,
        cache_dir=resolve_model_cache_dir(settings),
        batch_size=settings.embed_batch_size,
    )


def _production_counter(settings: Settings) -> TokenCounter:
    from app.embeddings.tokens import HuggingFaceTokenCounter

    return HuggingFaceTokenCounter(
        settings.embedding_model,
        cache_dir=resolve_model_cache_dir(settings),
    )
