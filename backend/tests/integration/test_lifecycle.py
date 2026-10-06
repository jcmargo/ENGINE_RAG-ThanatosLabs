"""Purga y borrado de empresa contra PostgreSQL. Fuera de la suite por defecto."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.domain.entities import TenantContext
from app.domain.enums import TenantRole
from app.domain.errors import ConflictError
from app.domain.lifecycle import tenant_tombstone_hmac
from app.infrastructure.database import create_database_engine, create_session_factory
from app.infrastructure.export_files import ExportFileStore
from app.infrastructure.local_files import LocalFileStore
from app.lifecycle.deletion import TenantDeletionService
from app.persistence.models.audit_event import AuditEventModel
from app.persistence.models.document import DocumentModel
from app.persistence.models.document_version import DocumentVersionModel
from app.persistence.models.history import ConversationModel, MessageModel
from app.persistence.models.lifecycle import TenantTombstoneModel
from app.persistence.models.tenant import TenantModel
from app.persistence.models.user import UserModel
from app.persistence.repositories.lifecycle import SqlAlchemyLifecycleRepository
from app.persistence.unit_of_work import UnitOfWork
from app.retention.service import RetentionService
from tests.auth.clock import FakeClock
from tests.factories.identity import make_membership, make_tenant, make_user
from tests.integration.db_helpers import (
    PERSISTENCE_DB,
    database_url_for,
    drop_and_create_database,
    drop_database,
    run_alembic,
    truncate_identity_tables,
)

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
_SECRET = "integration-pseudonym-secret-32b!!!"


@pytest.fixture(scope="module")
def lifecycle_database() -> Iterator[None]:
    """Base desechable para retención y borrado."""
    asyncio.run(drop_and_create_database(PERSISTENCE_DB))
    run_alembic("upgrade", "head", database_url=database_url_for(PERSISTENCE_DB))
    try:
        yield
    finally:
        asyncio.run(drop_database(PERSISTENCE_DB))


async def _open_factory() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_database_engine(database_url_for(PERSISTENCE_DB))
    factory = create_session_factory(engine)
    await truncate_identity_tables(factory)
    return engine, factory


def test_retention_dry_run_keeps_rows_and_purge_removes_only_expired(
    lifecycle_database: None,
) -> None:
    async def _run() -> None:
        engine, factory = await _open_factory()
        root = Path.cwd() / ".tmp-lifecycle-retention"
        try:
            tenant = make_tenant(slug="retencion-ficticia")
            user = make_user(email="retencion@example.com")
            async with UnitOfWork(factory) as unit:
                await unit.tenants.add(tenant)
                await unit.users.add(user)
                await unit.memberships.add(
                    make_membership(tenant_id=tenant.id, user_id=user.id, role=TenantRole.MEMBER)
                )
                await _conversation(unit.session, tenant.id, user.id, expired=True)
                await _conversation(unit.session, tenant.id, user.id, expired=False)
                await _document(unit.session, tenant.id)
                unit.session.add(
                    AuditEventModel(
                        id=uuid4(),
                        tenant_id=tenant.id,
                        actor_id=user.id,
                        actor_kind="user",
                        action="export",
                        resource_type="export",
                        resource_id=None,
                        result="success",
                        client_ip_prefix=None,
                        created_at=_NOW - timedelta(days=120),
                    )
                )
                unit.session.add(
                    AuditEventModel(
                        id=uuid4(),
                        tenant_id=tenant.id,
                        actor_id=user.id,
                        actor_kind="user",
                        action="export",
                        resource_type="export",
                        resource_id=None,
                        result="success",
                        client_ip_prefix=None,
                        created_at=_NOW,
                    )
                )
                await unit.commit()
            clock = FakeClock(_NOW)
            service = RetentionService(
                factory,
                ExportFileStore(root),
                conversation_days=30,
                audit_days=90,
                batch_size=100,
                clock=clock,
            )
            run_id = uuid4()
            async with UnitOfWork(factory) as unit:
                await SqlAlchemyLifecycleRepository(unit.session).begin_run(
                    run_id=run_id,
                    now=_NOW,
                    lock_seconds=3600,
                )
                await unit.commit()
            with pytest.raises(ConflictError):
                await service.purge_once(dry_run=True)
            async with UnitOfWork(factory) as unit:
                await SqlAlchemyLifecycleRepository(unit.session).finish_run(
                    run_id=run_id,
                    status="succeeded",
                    finished_at=_NOW,
                    manifest_sha256="ab" * 32,
                )
                await unit.commit()
            preview = await service.purge_once(dry_run=True)
            assert preview.conversations == 1
            assert preview.audit == 1
            assert preview.dry_run is True
            assert await _count(factory, ConversationModel) == 2
            removed = await service.purge_once(dry_run=False)
            assert removed.conversations == 1
            assert removed.audit == 1
            assert await _count(factory, ConversationModel) == 1
            assert await _count(factory, DocumentModel) == 1
            assert await _count(factory, AuditEventModel) == 1
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_deleting_tenant_a_leaves_tenant_b(lifecycle_database: None, tmp_path: Path) -> None:
    async def _run() -> None:
        engine, factory = await _open_factory()
        files = LocalFileStore(tmp_path)
        try:
            tenant_a = make_tenant(name="Empresa A", slug="empresa-a")
            tenant_b = make_tenant(name="Empresa B", slug="empresa-b")
            exclusive = make_user(email="exclusivo-a@example.com")
            shared = make_user(email="compartido@example.com")
            other = make_user(email="exclusivo-b@example.com")
            async with UnitOfWork(factory) as unit:
                await unit.tenants.add(tenant_a)
                await unit.tenants.add(tenant_b)
                await unit.users.add(exclusive)
                await unit.users.add(shared)
                await unit.users.add(other)
                await unit.memberships.add(
                    make_membership(
                        tenant_id=tenant_a.id,
                        user_id=exclusive.id,
                        role=TenantRole.TENANT_ADMIN,
                    )
                )
                await unit.memberships.add(
                    make_membership(
                        tenant_id=tenant_a.id, user_id=shared.id, role=TenantRole.MEMBER
                    )
                )
                await unit.memberships.add(
                    make_membership(
                        tenant_id=tenant_b.id, user_id=shared.id, role=TenantRole.MEMBER
                    )
                )
                await unit.memberships.add(
                    make_membership(
                        tenant_id=tenant_b.id,
                        user_id=other.id,
                        role=TenantRole.TENANT_ADMIN,
                    )
                )
                key_a = await _document(unit.session, tenant_a.id)
                key_b = await _document(unit.session, tenant_b.id)
                await unit.commit()
            _write(files, key_a)
            _write(files, key_b)
            points = _Points()
            service = TenantDeletionService(
                factory,
                points,
                files,
                ExportFileStore(tmp_path),
                pseudonym_secret=_SECRET,
                clock=FakeClock(_NOW),
            )
            await service.request(tenant_a.id)
            published = await service.confirm(tenant_a.id, tenant_a.slug)
            assert published is False
            assert points.deleted == [tenant_a.id]
            async with factory() as session:
                assert await session.get(TenantModel, tenant_a.id) is None
                remaining = await session.get(TenantModel, tenant_b.id)
                assert remaining is not None and remaining.slug == "empresa-b"
                assert await session.get(UserModel, exclusive.id) is None
                assert await session.get(UserModel, shared.id) is not None
                assert await session.get(UserModel, other.id) is not None
                documents = await session.scalars(
                    select(DocumentModel.tenant_id).where(DocumentModel.tenant_id == tenant_b.id)
                )
                assert list(documents.all()) == [tenant_b.id]
                tombstone = await session.scalar(select(TenantTombstoneModel))
            assert tombstone is not None
            assert tombstone.tenant_hmac == tenant_tombstone_hmac(_SECRET, tenant_a.id)
            assert "@" not in tombstone.tenant_hmac
            assert "exclusivo-a@example.com" not in tombstone.tenant_hmac
            assert files.exists(key_a) is False
            assert files.exists(key_b) is True
        finally:
            await engine.dispose()

    asyncio.run(_run())


class _Points:
    def __init__(self) -> None:
        self.deleted: list[UUID] = []

    async def delete_tenant(self, context: TenantContext) -> None:
        self.deleted.append(context.tenant_id)


async def _conversation(
    session: AsyncSession,
    tenant_id: UUID,
    user_id: UUID,
    *,
    expired: bool,
) -> None:
    conversation_id = uuid4()
    when = _NOW - timedelta(days=1) if expired else _NOW + timedelta(days=10)
    session.add(
        ConversationModel(
            id=conversation_id,
            tenant_id=tenant_id,
            owner_kind="user",
            owner_id=user_id,
            title="Conversacion",
            history_scope="visual",
            created_at=_NOW,
            updated_at=_NOW,
            expires_at=when,
        )
    )
    await session.flush()
    session.add(
        MessageModel(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            request_id=uuid4(),
            role="user",
            content="pregunta",
            citations=[],
            abstained=False,
            pipeline_version="test",
            created_at=_NOW,
        )
    )


async def _document(session: AsyncSession, tenant_id: UUID) -> str:
    document_id = uuid4()
    version_id = uuid4()
    key = f"{tenant_id}/{document_id}/{version_id}"
    session.add(
        DocumentModel(
            id=document_id,
            tenant_id=tenant_id,
            display_name="nota.md",
            status="ready",
            current_version_id=version_id,
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    await session.flush()
    session.add(
        DocumentVersionModel(
            id=version_id,
            tenant_id=tenant_id,
            document_id=document_id,
            version_number=1,
            status="ready",
            mime_type="text/markdown",
            byte_size=4,
            content_sha256="ab" * 32,
            storage_key=key,
            pipeline_version="test",
            chunk_count=1,
            progress=100,
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    return key


async def _count(factory: async_sessionmaker[AsyncSession], model: type) -> int:
    async with factory() as session:
        found = await session.scalar(select(func.count()).select_from(model))
    return int(found or 0)


def _write(files: LocalFileStore, key: str) -> None:
    target = files._root / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"original")
