"""Purga por lotes. Un dry-run cuenta y no borra."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.errors import ConflictError
from app.domain.lifecycle import RetentionOutcome
from app.infrastructure.export_files import ExportFileStore
from app.infrastructure.jwt_tokens import Clock, SystemClock
from app.persistence.repositories.lifecycle import SqlAlchemyLifecycleRepository
from app.persistence.unit_of_work import UnitOfWork


class RetentionService:
    """Conversaciones, trazas, auditoría y exportaciones vencidas. No toca documentos."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        export_files: ExportFileStore,
        *,
        conversation_days: int,
        audit_days: int,
        batch_size: int,
        clock: Clock | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._files = export_files
        self._conversation_days = conversation_days
        self._audit_days = audit_days
        self._batch_size = batch_size
        self._clock: Clock = clock if clock is not None else SystemClock()

    async def purge_once(self, *, dry_run: bool) -> RetentionOutcome:
        """Un lote. Si hay backup vigente, no borra."""
        now = self._clock.now()
        async with UnitOfWork(self._session_factory) as unit:
            repository = SqlAlchemyLifecycleRepository(unit.session)
            if await repository.mutations_blocked(now):
                raise ConflictError("Hay un backup en curso")
            if dry_run:
                return RetentionOutcome(
                    conversations=await repository.count_expired_conversations(now),
                    traces=await repository.count_expired_traces(now),
                    audit=await repository.count_expired_audit(
                        now - timedelta(days=self._audit_days)
                    ),
                    exports=len(
                        await repository.expired_export_keys(now=now, limit=self._batch_size)
                    ),
                    dry_run=True,
                )
            conversations = await repository.purge_expired_conversations(
                now=now,
                limit=self._batch_size,
            )
            traces = await repository.purge_expired_traces(now=now, limit=self._batch_size)
            audit = await repository.purge_expired_audit(
                cutoff=now - timedelta(days=self._audit_days),
                limit=self._batch_size,
            )
            expired = await repository.expired_export_keys(now=now, limit=self._batch_size)
            await repository.delete_exports([package_id for package_id, _key in expired])
            await unit.commit()
        for _package_id, key in expired:
            self._files.delete(key)
        return RetentionOutcome(
            conversations=conversations,
            traces=traces,
            audit=audit,
            exports=len(expired),
            dry_run=False,
        )
