"""Cola PostgreSQL. Cada método abre y cierra su propia transacción."""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.dml import Update

from app.domain.enums import DocumentVersionStatus, IngestionJobStatus, IngestionJobType
from app.ingestion.queue import ClaimedJob, RetryResult
from app.persistence.models.document import DocumentModel
from app.persistence.models.document_version import DocumentVersionModel
from app.persistence.models.ingestion_job import IngestionJobModel

_OPEN = (IngestionJobStatus.PENDING.value, IngestionJobStatus.LEASED.value)


class SqlAlchemyIngestionQueue:
    """Reclama trabajos de todas las empresas con `FOR UPDATE SKIP LOCKED`."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def claim_next(
        self,
        owner: str,
        now: datetime,
        lease_ttl: timedelta,
    ) -> ClaimedJob | None:
        async with self._session_factory() as session, session.begin():
            result = await session.execute(
                select(IngestionJobModel)
                .where(
                    or_(
                        and_(
                            IngestionJobModel.status == IngestionJobStatus.PENDING.value,
                            IngestionJobModel.available_at <= now,
                        ),
                        and_(
                            IngestionJobModel.status == IngestionJobStatus.LEASED.value,
                            IngestionJobModel.lease_expires_at <= now,
                        ),
                    )
                )
                .order_by(IngestionJobModel.available_at, IngestionJobModel.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            job = result.scalar_one_or_none()
            if job is None:
                return None
            job.status = IngestionJobStatus.LEASED.value
            job.lease_owner = owner
            job.lease_expires_at = now + lease_ttl
            job.updated_at = now
            if job.started_at is None:
                job.started_at = now
            if job.job_type == IngestionJobType.INGEST.value:
                await _mark_ingest_processing(session, job, now)
            return _claimed(job, owner)

    async def heartbeat(
        self,
        job_id: UUID,
        owner: str,
        now: datetime,
        lease_ttl: timedelta,
    ) -> bool:
        return await self._touch(
            job_id,
            owner,
            now,
            lease_expires_at=now + lease_ttl,
        )

    async def still_owned(self, job_id: UUID, owner: str, now: datetime) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                select(IngestionJobModel.id).where(
                    IngestionJobModel.id == job_id,
                    IngestionJobModel.status == IngestionJobStatus.LEASED.value,
                    IngestionJobModel.lease_owner == owner,
                    IngestionJobModel.lease_expires_at > now,
                )
            )
            return result.scalar_one_or_none() is not None

    async def set_progress(self, job_id: UUID, owner: str, progress: int, now: datetime) -> bool:
        return await self._touch(job_id, owner, now, progress=progress)

    async def ensure_replacement(
        self,
        job_id: UUID,
        owner: str,
        candidate: UUID,
        now: datetime,
    ) -> UUID | None:
        async with self._session_factory() as session, session.begin():
            job = await _leased(session, job_id, owner, now)
            if job is None:
                return None
            if job.replacement_version_id is None:
                job.replacement_version_id = candidate
                job.updated_at = now
            return job.replacement_version_id

    async def complete(self, job_id: UUID, owner: str, now: datetime) -> bool:
        return await self._finish(
            job_id,
            owner,
            now,
            status=IngestionJobStatus.SUCCEEDED.value,
            error_code=None,
            increment=False,
        )

    async def retry(
        self,
        job_id: UUID,
        owner: str,
        *,
        now: datetime,
        available_at: datetime,
        error_code: str,
    ) -> RetryResult:
        async with self._session_factory() as session, session.begin():
            job = await _leased(session, job_id, owner, now)
            if job is None:
                return RetryResult(accepted=False, dead=False)
            attempts = min(job.attempts + 1, job.max_attempts)
            dead = attempts >= job.max_attempts
            job.attempts = attempts
            job.error_code = error_code
            job.updated_at = now
            job.lease_owner = None
            job.lease_expires_at = None
            if dead:
                job.status = IngestionJobStatus.DEAD.value
                job.finished_at = now
            else:
                job.status = IngestionJobStatus.PENDING.value
                job.available_at = available_at
            return RetryResult(accepted=True, dead=dead)

    async def fail_permanent(
        self,
        job_id: UUID,
        owner: str,
        *,
        now: datetime,
        error_code: str,
    ) -> bool:
        return await self._finish(
            job_id,
            owner,
            now,
            status=IngestionJobStatus.DEAD.value,
            error_code=error_code,
            increment=True,
        )

    async def release(self, job_id: UUID, owner: str, now: datetime) -> bool:
        result = await self._execute(
            update(IngestionJobModel)
            .where(
                IngestionJobModel.id == job_id,
                IngestionJobModel.status == IngestionJobStatus.LEASED.value,
                IngestionJobModel.lease_owner == owner,
            )
            .values(
                status=IngestionJobStatus.PENDING.value,
                lease_owner=None,
                lease_expires_at=None,
                available_at=now,
                updated_at=now,
            )
        )
        return result

    async def finalize_consistent(self, now: datetime) -> int:
        ready_version = exists(
            select(DocumentVersionModel.id).where(
                DocumentVersionModel.id == IngestionJobModel.document_version_id,
                DocumentVersionModel.status == DocumentVersionStatus.READY.value,
            )
        )
        deleted_document = exists(
            select(DocumentModel.id).where(
                DocumentModel.id == IngestionJobModel.document_id,
                DocumentModel.status == DocumentVersionStatus.DELETED.value,
            )
        )
        ready_replacement = exists(
            select(DocumentVersionModel.id).where(
                DocumentVersionModel.id == IngestionJobModel.replacement_version_id,
                DocumentVersionModel.status == DocumentVersionStatus.READY.value,
            )
        )
        async with self._session_factory() as session, session.begin():
            result = await session.execute(
                update(IngestionJobModel)
                .where(
                    IngestionJobModel.status.in_(_OPEN),
                    or_(
                        and_(
                            IngestionJobModel.job_type == IngestionJobType.INGEST.value,
                            ready_version,
                        ),
                        and_(
                            IngestionJobModel.job_type == IngestionJobType.DELETE.value,
                            deleted_document,
                        ),
                        and_(
                            IngestionJobModel.job_type == IngestionJobType.REINDEX.value,
                            ready_replacement,
                        ),
                    ),
                )
                .values(
                    status=IngestionJobStatus.SUCCEEDED.value,
                    lease_owner=None,
                    lease_expires_at=None,
                    progress=100,
                    error_code=None,
                    finished_at=now,
                    updated_at=now,
                )
            )
            return _rowcount(result)

    async def list_referenced_storage_keys(self) -> frozenset[str]:
        """Todas las claves de `document_versions`, sin filtrar por estado."""
        async with self._session_factory() as session:
            result = await session.scalars(select(DocumentVersionModel.storage_key))
            return frozenset(result.all())

    async def _touch(
        self,
        job_id: UUID,
        owner: str,
        now: datetime,
        *,
        progress: int | None = None,
        lease_expires_at: datetime | None = None,
    ) -> bool:
        values: dict[str, object] = {"updated_at": now}
        if progress is not None:
            values["progress"] = progress
        if lease_expires_at is not None:
            values["lease_expires_at"] = lease_expires_at
        return await self._execute(
            update(IngestionJobModel)
            .where(
                IngestionJobModel.id == job_id,
                IngestionJobModel.status == IngestionJobStatus.LEASED.value,
                IngestionJobModel.lease_owner == owner,
                IngestionJobModel.lease_expires_at > now,
            )
            .values(**values)
        )

    async def _finish(
        self,
        job_id: UUID,
        owner: str,
        now: datetime,
        *,
        status: str,
        error_code: str | None,
        increment: bool,
    ) -> bool:
        async with self._session_factory() as session, session.begin():
            job = await _leased(session, job_id, owner, now)
            if job is None:
                return False
            if increment:
                job.attempts = min(job.attempts + 1, job.max_attempts)
            job.status = status
            job.error_code = error_code
            job.progress = 100 if status == IngestionJobStatus.SUCCEEDED.value else job.progress
            job.lease_owner = None
            job.lease_expires_at = None
            job.finished_at = now
            job.updated_at = now
            return True

    async def _execute(self, statement: Update) -> bool:
        async with self._session_factory() as session, session.begin():
            result = await session.execute(statement)
            return _rowcount(result) == 1


def _claimed(job: IngestionJobModel, owner: str) -> ClaimedJob:
    return ClaimedJob(
        id=job.id,
        tenant_id=job.tenant_id,
        document_id=job.document_id,
        document_version_id=job.document_version_id,
        job_type=IngestionJobType(job.job_type),
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        progress=job.progress,
        replacement_version_id=job.replacement_version_id,
        lease_owner=owner,
        origin_request_id=job.origin_request_id,
    )


async def _leased(
    session: AsyncSession,
    job_id: UUID,
    owner: str,
    now: datetime,
) -> IngestionJobModel | None:
    result = await session.execute(
        select(IngestionJobModel)
        .where(
            IngestionJobModel.id == job_id,
            IngestionJobModel.status == IngestionJobStatus.LEASED.value,
            IngestionJobModel.lease_owner == owner,
            IngestionJobModel.lease_expires_at > now,
        )
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def _mark_ingest_processing(
    session: AsyncSession,
    job: IngestionJobModel,
    now: datetime,
) -> None:
    version = await session.get(DocumentVersionModel, job.document_version_id)
    if version is None or version.status != DocumentVersionStatus.QUEUED.value:
        return
    version.status = DocumentVersionStatus.PROCESSING.value
    version.updated_at = now
    document = await session.get(DocumentModel, job.document_id)
    if document is None or document.current_version_id != version.id:
        return
    if document.status == DocumentVersionStatus.QUEUED.value:
        document.status = DocumentVersionStatus.PROCESSING.value
        document.updated_at = now


def _rowcount(result: object) -> int:
    return int(getattr(result, "rowcount", 0))
