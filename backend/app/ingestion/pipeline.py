"""Pipelines de ingesta, reindexación y borrado. Cada etapa se puede repetir."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.documents.chunking import TokenCounter, chunk_document, document_content_hash
from app.documents.models import Chunk
from app.documents.parsers.base import EmptyDocumentError
from app.documents.parsers.registry import parse_document
from app.documents.store import FileStore
from app.documents.versions import REINDEX_PIPELINE_VERSION
from app.domain.entities import DocumentVersion, TenantContext
from app.domain.enums import DocumentVersionStatus, IngestionJobType, UsageKind
from app.domain.errors import InvalidStateError, NotFoundError
from app.embeddings.base import EmbeddingProvider
from app.ingestion.context import context_for_job
from app.ingestion.failures import ContentHashMismatchError, LeaseLost, MissingOriginalError
from app.ingestion.queue import ClaimedJob, JobQueue
from app.persistence.repositories.documents import SqlAlchemyDocumentRepository
from app.persistence.unit_of_work import UnitOfWork
from app.persistence.usage import record_usage_event
from app.retrieval.vectors import VectorRecord, VectorStore

logger = logging.getLogger(__name__)

_EMBED_PROGRESS_END = 80
_UPSERT_PROGRESS_START = 81
_UPSERT_PROGRESS_END = 89
_UPSERT_REPORT_BATCH = 64
_INDEXED_PROGRESS = 90
_READY_PROGRESS = 100

ProgressReport = Callable[[int], Awaitable[None]]


class Clock(Protocol):
    """Reloj inyectable para el backoff y el lease."""

    def now(self) -> datetime:
        """Instante actual en UTC."""


@dataclass
class PipelineHooks:
    """Ganchos de prueba. El de upsert simula una caída del proceso."""

    after_upsert: Callable[[], Awaitable[None]] | None = None


class DocumentPipeline:
    """Ejecuta un trabajo ya alquilado sin mantener abierta la transacción SQL."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        file_store: FileStore,
        vectors: VectorStore,
        embeddings: EmbeddingProvider,
        counter: TokenCounter,
        queue: JobQueue,
        *,
        embed_batch_size: int,
        clock: Clock,
        hooks: PipelineHooks | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._files = file_store
        self._vectors = vectors
        self._embeddings = embeddings
        self._counter = counter
        self._queue = queue
        self._embed_batch_size = embed_batch_size
        self._clock = clock
        self._hooks = hooks if hooks is not None else PipelineHooks()

    async def run(self, job: ClaimedJob, *, lease_lost: asyncio.Event) -> None:
        started = time.perf_counter()
        if job.job_type is IngestionJobType.INGEST:
            await self._ingest(job, lease_lost)
        elif job.job_type is IngestionJobType.REINDEX:
            await self._reindex(job, lease_lost)
        else:
            await self._delete(job, lease_lost)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "trabajo terminado job=%s document=%s type=%s duration_ms=%d",
            job.id,
            job.document_id,
            job.job_type.value,
            elapsed_ms,
        )

    async def _ingest(self, job: ClaimedJob, lease_lost: asyncio.Event) -> None:
        _log_stage(job, "ingest")
        context = context_for_job(job.tenant_id)
        version, display_name = await self._load(job)
        if version.status is DocumentVersionStatus.READY:
            await self._owned(job, lease_lost)
            await self._complete(job)
            return
        if version.indexed_at is None:
            published = 0

            async def report(progress: int) -> None:
                nonlocal published
                if progress <= published:
                    return
                published = progress
                await self._progress(job, version, progress, lease_lost)

            records = await self._records(version, display_name, version.id, report=report)
            await self._upsert_records(context, records, report)
            await self._after_upsert()
            await self._owned(job, lease_lost)
            async with self._documents(job.tenant_id) as (repository, unit):
                await repository.mark_indexed(
                    document_id=job.document_id,
                    version_id=version.id,
                    chunk_count=len(records),
                    progress=_INDEXED_PROGRESS,
                    indexed_at=self._clock.now(),
                )
                await unit.commit()
            chunk_count = len(records)
        else:
            chunk_count = version.chunk_count
        await self._owned(job, lease_lost)
        await self._vectors.activate_version(
            context,
            document_id=job.document_id,
            document_version_id=version.id,
        )
        await self._owned(job, lease_lost)
        ready_at = self._clock.now()
        async with self._documents(job.tenant_id) as (repository, unit):
            await repository.mark_ready(
                document_id=job.document_id,
                version_id=version.id,
                chunk_count=chunk_count,
                updated_at=ready_at,
            )
            await record_usage_event(
                unit,
                context,
                kind=UsageKind.CHUNKS,
                source_id=version.id,
                occurred_at=ready_at,
                quantity=chunk_count,
            )
            await unit.commit()
        await self._complete(job)

    async def _reindex(self, job: ClaimedJob, lease_lost: asyncio.Event) -> None:
        _log_stage(job, "reindex")
        context = context_for_job(job.tenant_id)
        source, display_name = await self._load(job)
        if source.status is DocumentVersionStatus.SUPERSEDED:
            await self._owned(job, lease_lost)
            await self._complete(job)
            return
        replacement_id = await self._queue.ensure_replacement(
            job.id,
            job.lease_owner,
            uuid4(),
            self._clock.now(),
        )
        if replacement_id is None:
            raise LeaseLost
        if await self._replacement_ready(job, replacement_id):
            await self._vectors.activate_version(
                context,
                document_id=job.document_id,
                document_version_id=replacement_id,
            )
            await self._owned(job, lease_lost)
            await self._complete(job)
            return
        records = await self._records(source, display_name, replacement_id)
        await self._owned(job, lease_lost)
        await self._upsert_records(context, records, None)
        await self._after_upsert()
        await self._owned(job, lease_lost)
        await self._vectors.activate_version(
            context,
            document_id=job.document_id,
            document_version_id=replacement_id,
        )
        await self._owned(job, lease_lost)
        replacement = _replacement_version(
            source,
            version_id=replacement_id,
            chunk_count=len(records),
            now=self._clock.now(),
        )
        ready_at = self._clock.now()
        async with self._documents(job.tenant_id) as (repository, unit):
            await repository.commit_replacement(
                document_id=job.document_id,
                source_version_id=source.id,
                replacement=replacement,
                updated_at=ready_at,
            )
            await record_usage_event(
                unit,
                context,
                kind=UsageKind.CHUNKS,
                source_id=replacement_id,
                occurred_at=ready_at,
                quantity=len(records),
            )
            await unit.commit()
        await self._complete(job)

    async def _delete(self, job: ClaimedJob, lease_lost: asyncio.Event) -> None:
        _log_stage(job, "delete")
        context = context_for_job(job.tenant_id)
        keys = await self._storage_keys(job)
        await self._vectors.delete_document(context, document_id=job.document_id)
        if job.replacement_version_id is not None:
            await self._vectors.delete_version(
                context,
                document_id=job.document_id,
                document_version_id=job.replacement_version_id,
            )
        for key in keys:
            await asyncio.to_thread(self._files.delete, key)
        await self._owned(job, lease_lost)
        try:
            async with self._documents(job.tenant_id) as (repository, unit):
                await repository.mark_document_deleted(job.document_id, self._clock.now())
                await unit.commit()
        except NotFoundError:
            pass
        await self._complete(job)

    async def _records(
        self,
        version: DocumentVersion,
        display_name: str,
        point_version_id: UUID,
        *,
        report: ProgressReport | None = None,
    ) -> tuple[VectorRecord, ...]:
        chunks, parser_version = await asyncio.to_thread(
            self._prepare_chunks,
            version,
            display_name,
            point_version_id,
        )
        texts = [chunk.text for chunk in chunks]
        from app.infrastructure.metrics import observe_tokens

        observe_tokens("ingestion", sum(len(item) for item in texts))
        vectors = await self._embed_vectors(texts, report)
        return tuple(
            _vector_record(
                version,
                display_name,
                point_version_id,
                chunk,
                parser_version,
                self._embeddings.model_version,
                vector,
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        )

    def _prepare_chunks(
        self,
        version: DocumentVersion,
        display_name: str,
        point_version_id: UUID,
    ) -> tuple[tuple[Chunk, ...], str]:
        try:
            with self._files.open_read(version.storage_key) as handle:
                content = handle.read()
        except InvalidStateError as error:
            raise MissingOriginalError from error
        digest = document_content_hash(content)
        if digest != version.content_sha256:
            raise ContentHashMismatchError
        extracted = parse_document(display_name, content, version.mime_type)
        chunks = chunk_document(
            extracted,
            document_hash=digest,
            document_version=str(point_version_id),
            counter=self._counter,
        )
        if not chunks:
            message = "El documento no contiene texto utilizable"
            raise EmptyDocumentError(message)
        return chunks, extracted.parser_version

    async def _embed_vectors(
        self,
        texts: Sequence[str],
        report: ProgressReport | None,
    ) -> list[list[float]]:
        vectors: list[list[float]] = []
        total = len(texts)
        for start in range(0, total, self._embed_batch_size):
            batch = texts[start : start + self._embed_batch_size]
            vectors.extend(await asyncio.to_thread(self._embeddings.embed_documents, batch))
            if report is not None:
                await report(embedding_progress(len(vectors), total))
        return vectors

    async def _upsert_records(
        self,
        context: TenantContext,
        records: Sequence[VectorRecord],
        report: ProgressReport | None,
    ) -> None:
        total = len(records)
        done = 0
        for start in range(0, total, _UPSERT_REPORT_BATCH):
            batch = records[start : start + _UPSERT_REPORT_BATCH]
            await self._vectors.upsert(context, batch)
            done += len(batch)
            if report is not None:
                await report(upsert_progress(done, total))

    async def _load(self, job: ClaimedJob) -> tuple[DocumentVersion, str]:
        async with self._documents(job.tenant_id) as (repository, _unit):
            document, _current = await repository.get_document(job.document_id)
            version = await repository.get_version(job.document_id, job.document_version_id)
            return version, document.display_name

    async def _storage_keys(self, job: ClaimedJob) -> tuple[str, ...]:
        try:
            async with self._documents(job.tenant_id) as (repository, _unit):
                return await repository.list_storage_keys(job.document_id)
        except NotFoundError:
            return ()

    async def _replacement_ready(self, job: ClaimedJob, replacement_id: UUID) -> bool:
        try:
            async with self._documents(job.tenant_id) as (repository, _unit):
                version = await repository.get_version(job.document_id, replacement_id)
        except NotFoundError:
            return False
        return version.status is DocumentVersionStatus.READY

    async def _progress(
        self,
        job: ClaimedJob,
        version: DocumentVersion,
        progress: int,
        lease_lost: asyncio.Event,
    ) -> None:
        await self._owned(job, lease_lost)
        if not await self._queue.set_progress(
            job.id,
            job.lease_owner,
            progress,
            self._clock.now(),
        ):
            raise LeaseLost
        async with self._documents(job.tenant_id) as (repository, unit):
            await repository.set_version_progress(
                document_id=job.document_id,
                version_id=version.id,
                progress=progress,
                updated_at=self._clock.now(),
            )
            await unit.commit()

    async def _complete(self, job: ClaimedJob) -> None:
        if not await self._queue.complete(job.id, job.lease_owner, self._clock.now()):
            raise LeaseLost

    async def _after_upsert(self) -> None:
        if self._hooks.after_upsert is not None:
            await self._hooks.after_upsert()

    async def _owned(self, job: ClaimedJob, lease_lost: asyncio.Event) -> None:
        if lease_lost.is_set() or not await self._queue.still_owned(
            job.id,
            job.lease_owner,
            self._clock.now(),
        ):
            raise LeaseLost

    @asynccontextmanager
    async def _documents(
        self,
        tenant_id: UUID,
    ) -> AsyncIterator[tuple[SqlAlchemyDocumentRepository, UnitOfWork]]:
        context = context_for_job(tenant_id)
        async with UnitOfWork(self._session_factory) as unit:
            yield SqlAlchemyDocumentRepository(unit, context), unit


def _log_stage(job: ClaimedJob, stage: str) -> None:
    from app.infrastructure.metrics import observe_ingestion

    observe_ingestion(stage)
    logger.info(
        "etapa job=%s document=%s stage=%s",
        job.id,
        job.document_id,
        stage,
    )


def embedding_progress(done: int, total: int) -> int:
    """Avance visible mientras se embebe. Termina en 80, antes del upsert."""
    return _ranged_progress(done, total, start=1, end=_EMBED_PROGRESS_END)


def upsert_progress(done: int, total: int) -> int:
    """Avance visible mientras se escriben los puntos. Termina en 89."""
    return _ranged_progress(done, total, start=_UPSERT_PROGRESS_START, end=_UPSERT_PROGRESS_END)


def _ranged_progress(done: int, total: int, *, start: int, end: int) -> int:
    if done < 1 or total < 1:
        return 0
    completed = min(done, total)
    return start + ((end - start) * completed) // total


def _vector_record(
    version: DocumentVersion,
    display_name: str,
    point_version_id: UUID,
    chunk: Chunk,
    parser_version: str,
    embedding_version: str,
    vector: Sequence[float],
) -> VectorRecord:
    return VectorRecord(
        tenant_id=version.tenant_id,
        document_id=version.document_id,
        document_version_id=point_version_id,
        chunk_id=chunk.id,
        source=display_name,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        section=chunk.section,
        position=chunk.position,
        text=chunk.text,
        parser_version=parser_version,
        chunker_version=chunk.chunker_version,
        embedding_version=embedding_version,
        vector=tuple(vector),
        is_active=False,
    )


def _replacement_version(
    source: DocumentVersion,
    *,
    version_id: UUID,
    chunk_count: int,
    now: datetime,
) -> DocumentVersion:
    return DocumentVersion(
        id=version_id,
        tenant_id=source.tenant_id,
        document_id=source.document_id,
        version_number=source.version_number + 1,
        status=DocumentVersionStatus.READY,
        mime_type=source.mime_type,
        byte_size=source.byte_size,
        content_sha256=source.content_sha256,
        storage_key=source.storage_key,
        pipeline_version=REINDEX_PIPELINE_VERSION,
        chunk_count=chunk_count,
        created_at=now,
        updated_at=now,
        progress=_READY_PROGRESS,
        indexed_at=now,
    )
