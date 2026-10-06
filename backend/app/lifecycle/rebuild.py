"""Encola la reindexación de versiones ready. No borra el estado de negocio."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.entities import IngestionJob
from app.domain.enums import IngestionJobStatus, IngestionJobType
from app.persistence.mappers import ingestion_job_to_model
from app.persistence.models.ingestion_job import IngestionJobModel
from app.persistence.repositories.lifecycle import SqlAlchemyLifecycleRepository
from app.persistence.unit_of_work import UnitOfWork


async def enqueue_ready_reindex(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    max_attempts: int,
) -> int:
    """Crea un trabajo de reindexación por versión lista que aún no lo tenga."""
    async with UnitOfWork(session_factory) as unit:
        repository = SqlAlchemyLifecycleRepository(unit.session)
        versions = await repository.ready_versions()
        created = 0
        for tenant_id, document_id, version_id in versions:
            existing = await unit.session.execute(
                select(IngestionJobModel.id)
                .where(
                    IngestionJobModel.document_version_id == version_id,
                    IngestionJobModel.job_type == IngestionJobType.REINDEX.value,
                    IngestionJobModel.status.in_(("pending", "leased")),
                )
                .limit(1)
            )
            if existing.scalar_one_or_none() is not None:
                continue
            unit.session.add(
                ingestion_job_to_model(
                    IngestionJob(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        document_id=document_id,
                        document_version_id=version_id,
                        job_type=IngestionJobType.REINDEX,
                        status=IngestionJobStatus.PENDING,
                        created_at=now,
                        updated_at=now,
                        max_attempts=max_attempts,
                        available_at=now,
                    )
                )
            )
            created += 1
        await unit.commit()
    return created
