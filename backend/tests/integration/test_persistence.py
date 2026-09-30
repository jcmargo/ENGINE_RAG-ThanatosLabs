"""Persistencia de identidad contra PostgreSQL real. Fuera de la suite por defecto."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.domain.entities import Membership, Tenant, User
from app.domain.enums import SystemRole, TenantRole, TenantStatus, UserStatus
from app.domain.errors import ConflictError, NotFoundError
from app.infrastructure.database import create_database_engine, create_session_factory
from app.persistence.models import TenantModel
from app.persistence.unit_of_work import UnitOfWork
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


@pytest.fixture(scope="module")
def persistence_database() -> Iterator[None]:
    """Crea la base desechable una vez; cada test abre su propio engine."""
    asyncio.run(drop_and_create_database(PERSISTENCE_DB))
    run_alembic("upgrade", "head", database_url=database_url_for(PERSISTENCE_DB))
    try:
        yield
    finally:
        asyncio.run(drop_database(PERSISTENCE_DB))


async def _open_factory() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Engine y fábrica en el bucle actual; el caller debe hacer dispose."""
    engine = create_database_engine(database_url_for(PERSISTENCE_DB))
    factory = create_session_factory(engine)
    await truncate_identity_tables(factory)
    return engine, factory


def test_duplicate_slug_email_and_membership_are_rejected(
    persistence_database: None,
) -> None:
    async def _run() -> None:
        engine, session_factory = await _open_factory()
        try:
            tenant = make_tenant(slug="norte-ficticio")
            user = make_user(email="carla.demo@example.com")
            async with UnitOfWork(session_factory) as uow:
                await uow.tenants.add(tenant)
                await uow.users.add(user)
                await uow.commit()

            async with UnitOfWork(session_factory) as uow:
                with pytest.raises(ConflictError) as slug_error:
                    await uow.tenants.add(make_tenant(slug="Norte Ficticio"))
                assert slug_error.value.code == "conflict"
                await uow.rollback()

            async with UnitOfWork(session_factory) as uow:
                with pytest.raises(ConflictError) as email_error:
                    await uow.users.add(make_user(email="  CARLA.DEMO@EXAMPLE.COM "))
                assert email_error.value.code == "conflict"
                await uow.rollback()

            membership = make_membership(
                tenant_id=tenant.id,
                user_id=user.id,
                role=TenantRole.MEMBER,
            )
            async with UnitOfWork(session_factory) as uow:
                await uow.memberships.add(membership)
                await uow.commit()

            async with UnitOfWork(session_factory) as uow:
                with pytest.raises(ConflictError) as membership_error:
                    await uow.memberships.add(
                        make_membership(
                            tenant_id=tenant.id,
                            user_id=user.id,
                            role=TenantRole.TENANT_ADMIN,
                        )
                    )
                assert membership_error.value.code == "conflict"
                await uow.rollback()
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_unit_of_work_rolls_back_failed_transaction(persistence_database: None) -> None:
    async def _run() -> None:
        engine, session_factory = await _open_factory()
        try:
            committed = make_tenant(name="Empresa Comprometida SA", slug="empresa-comprometida")
            async with UnitOfWork(session_factory) as uow:
                await uow.tenants.add(committed)
                await uow.commit()

            async with UnitOfWork(session_factory) as uow:
                await uow.tenants.add(make_tenant(name="Temporal SA", slug="temporal-sa"))
                with pytest.raises(ConflictError):
                    await uow.tenants.add(
                        make_tenant(name="Duplicada SA", slug="empresa-comprometida")
                    )
                await uow.rollback()

            async with UnitOfWork(session_factory) as uow:
                kept = await uow.tenants.get_by_slug("empresa-comprometida")
                assert kept.id == committed.id
                assert await uow.tenants.exists_by_slug("temporal-sa") is False
                with pytest.raises(NotFoundError):
                    await uow.tenants.get_by_slug("temporal-sa")
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_timestamps_keep_utc_offset(persistence_database: None) -> None:
    async def _run() -> None:
        engine, session_factory = await _open_factory()
        try:
            stamp = make_tenant().created_at
            assert stamp.tzinfo is not None
            assert stamp.utcoffset() == timedelta(0)
            tenant = make_tenant(created_at=stamp, updated_at=stamp)
            async with UnitOfWork(session_factory) as uow:
                saved = await uow.tenants.add(tenant)
                await uow.commit()

            async with UnitOfWork(session_factory) as uow:
                loaded = await uow.tenants.get_by_id(saved.id)

            assert loaded.created_at.tzinfo is not None
            assert loaded.created_at.utcoffset() == timedelta(0)
            assert loaded.updated_at.tzinfo is not None
            assert loaded.updated_at.utcoffset() == timedelta(0)
            assert loaded.created_at.astimezone(UTC) == stamp.astimezone(UTC)
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_repositories_return_domain_entities_not_orm(persistence_database: None) -> None:
    async def _run() -> None:
        engine, session_factory = await _open_factory()
        try:
            tenant = make_tenant(slug="dominio-puro")
            provider = make_user(
                email="provider.admin@example.com",
                system_role=SystemRole.PROVIDER_ADMIN,
            )
            member = make_user(email="miembro@example.com")

            async with UnitOfWork(session_factory) as uow:
                saved_tenant = await uow.tenants.add(tenant)
                saved_provider = await uow.users.add(provider)
                saved_member = await uow.users.add(member)
                membership = await uow.memberships.add(
                    make_membership(
                        tenant_id=saved_tenant.id,
                        user_id=saved_member.id,
                        role=TenantRole.TENANT_ADMIN,
                    )
                )
                await uow.commit()

            async with UnitOfWork(session_factory) as uow:
                by_id = await uow.tenants.get_by_id(saved_tenant.id)
                by_slug = await uow.tenants.get_by_slug("Dominio Puro")
                by_email = await uow.users.get_by_email("  Provider.Admin@Example.com ")
                listed = await uow.memberships.list_by_tenant(saved_tenant.id)
                by_user = await uow.memberships.list_by_user(saved_member.id)
                loaded_membership = await uow.memberships.get_by_id(membership.id)

            assert isinstance(by_id, Tenant)
            assert isinstance(by_slug, Tenant)
            assert isinstance(by_email, User)
            assert isinstance(loaded_membership, Membership)
            assert type(by_id) is Tenant
            assert not isinstance(by_id, TenantModel)
            assert by_email.system_role == SystemRole.PROVIDER_ADMIN
            assert by_email.id == saved_provider.id
            assert listed == [loaded_membership]
            assert by_user == [loaded_membership]
            assert by_slug.slug == "dominio-puro"

            async with UnitOfWork(session_factory) as uow:
                with pytest.raises(NotFoundError):
                    await uow.memberships.add(
                        make_membership(tenant_id=uuid4(), user_id=saved_member.id)
                    )
                await uow.rollback()

            async with UnitOfWork(session_factory) as uow:
                updated = await uow.tenants.update_status(saved_tenant.id, TenantStatus.SUSPENDED)
                await uow.users.update_status(saved_provider.id, UserStatus.DISABLED)
                await uow.commit()

            assert updated.status == TenantStatus.SUSPENDED
            async with UnitOfWork(session_factory) as uow:
                assert (await uow.users.get_by_id(saved_provider.id)).status == UserStatus.DISABLED
        finally:
            await engine.dispose()

    asyncio.run(_run())
