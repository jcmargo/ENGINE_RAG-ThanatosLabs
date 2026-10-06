"""Eliminación de empresa en dos fases y reconciliación de tombstones."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.backup.ports import BackupPublisher
from app.backup.service import publish_tombstones
from app.documents.store import FileStore
from app.domain.entities import Tenant, TenantContext
from app.domain.enums import TenantStatus
from app.domain.errors import InvalidStateError, NotFoundError
from app.domain.lifecycle import Tombstone, tenant_tombstone_hmac
from app.infrastructure.export_files import ExportFileStore
from app.infrastructure.jwt_tokens import Clock, SystemClock
from app.ingestion.context import context_for_job
from app.persistence.repositories.lifecycle import SqlAlchemyLifecycleRepository
from app.persistence.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)


class TenantPointDeleter(Protocol):
    """Borra los puntos de la empresa del contexto."""

    async def delete_tenant(self, context: TenantContext) -> None:
        """Elimina solo esa empresa."""


_BATCH = 100
_CONFIRMATION = "La confirmación no coincide con la empresa"


class TenantDeletionService:
    """Marca, revoca y, tras la confirmación del proveedor, borra."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        vectors: TenantPointDeleter,
        files: FileStore,
        export_files: ExportFileStore,
        *,
        pseudonym_secret: str,
        publisher: BackupPublisher | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._vectors = vectors
        self._files = files
        self._export_files = export_files
        self._secret = pseudonym_secret
        self._publisher = publisher
        self._clock: Clock = clock if clock is not None else SystemClock()

    async def request(self, tenant_id: UUID) -> Tenant:
        """Pasa a deleting y revoca sesiones y API keys."""
        now = self._clock.now()
        async with UnitOfWork(self._session_factory) as unit:
            await SqlAlchemyLifecycleRepository(unit.session).assert_mutations_allowed(now)
            tenant = await unit.tenants.update_status(tenant_id, TenantStatus.DELETING)
            await SqlAlchemyLifecycleRepository(unit.session).revoke_access(tenant_id, now=now)
            await unit.commit()
        logger.info("tenant deletion status=requested tenant=%s", tenant_id)
        return tenant

    async def confirm(self, tenant_id: UUID, confirmation: str) -> bool:
        """Exige el slug y borra. Devuelve si el tombstone llegó a restic."""
        now = self._clock.now()
        async with UnitOfWork(self._session_factory) as unit:
            await SqlAlchemyLifecycleRepository(unit.session).assert_mutations_allowed(now)
            tenant = await unit.tenants.get_by_id(tenant_id)
        if tenant.status is not TenantStatus.DELETING:
            message = "La empresa no está en eliminación"
            raise InvalidStateError(message)
        if confirmation != tenant.slug:
            raise InvalidStateError(_CONFIRMATION)
        tombstone = Tombstone(
            tenant_hmac=tenant_tombstone_hmac(self._secret, tenant_id),
            deleted_at=now,
        )
        await self.purge(tenant_id, tombstone=tombstone)
        try:
            return await publish_tombstones(self._session_factory, self._publisher)
        except InvalidStateError:
            logger.info("tombstone restic status=error")
            return False

    async def purge(self, tenant_id: UUID, *, tombstone: Tombstone) -> None:
        """Borra puntos, archivos y filas. Un reintento sigue siendo válido."""
        await self._vectors.delete_tenant(context_for_job(tenant_id))
        async with UnitOfWork(self._session_factory) as unit:
            repository = SqlAlchemyLifecycleRepository(unit.session)
            storage_keys = await repository.storage_keys(tenant_id)
            export_keys = await repository.export_keys(tenant_id)
        for key in storage_keys:
            self._files.delete(key)
        for key in export_keys:
            self._export_files.delete(key)
        while True:
            async with UnitOfWork(self._session_factory) as unit:
                removed = await SqlAlchemyLifecycleRepository(unit.session).purge_tenant_batch(
                    tenant_id,
                    limit=_BATCH,
                )
                await unit.commit()
            if removed == 0:
                break
        async with UnitOfWork(self._session_factory) as unit:
            await SqlAlchemyLifecycleRepository(unit.session).finish_tenant_purge(
                tenant_id,
                tombstone=tombstone,
            )
            await unit.commit()
        logger.info("tenant deletion status=purged hmac=%s", tombstone.tenant_hmac)


async def reconcile_tombstones(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    secret: str,
    rows: tuple[Tombstone, ...],
    vectors: TenantPointDeleter,
    files: FileStore,
) -> int:
    """Vuelve a borrar empresas que un backup antiguo haya resucitado."""
    if not rows:
        return 0
    export_files = ExportFileStore(_file_root(files))
    service = TenantDeletionService(
        session_factory,
        vectors,
        files,
        export_files,
        pseudonym_secret=secret,
    )
    async with UnitOfWork(session_factory) as unit:
        tenant_ids = await SqlAlchemyLifecycleRepository(unit.session).tenant_ids()
    applied = 0
    indexed = {row.tenant_hmac: row for row in rows}
    for tenant_id in tenant_ids:
        digest = tenant_tombstone_hmac(secret, tenant_id)
        tombstone = indexed.get(digest)
        if tombstone is None:
            continue
        try:
            await service.purge(tenant_id, tombstone=tombstone)
        except NotFoundError:
            continue
        applied += 1
    async with UnitOfWork(session_factory) as unit:
        repository = SqlAlchemyLifecycleRepository(unit.session)
        for row in rows:
            await repository.upsert_tombstone(row)
        await unit.commit()
    logger.info("tombstone reconcile applied=%d", applied)
    return applied


def _file_root(files: FileStore) -> Path:
    root = getattr(files, "_root", None)
    if not isinstance(root, Path):
        message = "El almacén de archivos no tiene raíz"
        raise InvalidStateError(message)
    return root
