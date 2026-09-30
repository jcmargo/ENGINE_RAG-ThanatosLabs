"""Autenticación, RBAC y API keys contra PostgreSQL real."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.deps import (
    require_authenticated,
    require_provider_admin,
    require_scope,
    require_tenant_admin,
)
from app.config import Environment, LogLevel, Settings
from app.domain.entities import AuthPrincipal, Tenant, User
from app.domain.enums import SystemRole, TenantRole, TenantStatus, UserStatus
from app.infrastructure.database import create_database_engine, create_session_factory
from app.infrastructure.passwords import Argon2Policy, PasswordHasher
from app.main import create_app
from app.persistence.unit_of_work import UnitOfWork
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

_PASSWORD = "integration-password-not-logged"
_OTHER_PASSWORD = "other-password-not-logged"


@dataclass(frozen=True)
class _SeededIdentity:
    tenant_a: Tenant
    tenant_b: Tenant
    admin_a: User
    member_a: User


@pytest.fixture(scope="module")
def auth_database() -> Iterator[None]:
    """Base desechable; se recrea limpia al inicio del módulo."""
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


def _settings(url: str) -> Settings:
    return Settings(
        environment=Environment.TEST,
        log_level=LogLevel.INFO,
        database_url=url,
        jwt_secret="integration-test-jwt-secret-32b!!",
    )


def _mount_matrix_routes(application: FastAPI) -> None:
    """Monta dependencias de producción sobre la app real para la matriz RBAC."""

    @application.get("/__test__/provider")
    async def provider_only(
        _principal: Annotated[AuthPrincipal, Depends(require_provider_admin)],
    ) -> dict[str, str]:
        return {"ok": "provider"}

    @application.get("/__test__/tenant-admin")
    async def tenant_admin_only(
        _principal: Annotated[AuthPrincipal, Depends(require_tenant_admin)],
    ) -> dict[str, str]:
        return {"ok": "tenant_admin"}

    @application.get("/__test__/scope-query")
    async def scope_query(
        _principal: Annotated[AuthPrincipal, Depends(require_scope("query"))],
    ) -> dict[str, str]:
        return {"ok": "query"}

    @application.get("/__test__/whoami")
    async def whoami(
        principal: Annotated[AuthPrincipal, Depends(require_authenticated)],
    ) -> dict[str, str | None]:
        return {
            "type": principal.type.value,
            "tenant_id": str(principal.tenant_id) if principal.tenant_id else None,
        }


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_identity(
    factory: async_sessionmaker[AsyncSession],
) -> _SeededIdentity:
    hasher = PasswordHasher()
    tenant_a = make_tenant(slug="empresa-a", name="Empresa A")
    tenant_b = make_tenant(slug="empresa-b", name="Empresa B")
    provider = make_user(
        email="provider.admin@example.com",
        password_hash=hasher.hash(_PASSWORD),
        system_role=SystemRole.PROVIDER_ADMIN,
    )
    admin_a = make_user(
        email="admin.a@example.com",
        password_hash=hasher.hash(_PASSWORD),
    )
    member_a = make_user(
        email="member.a@example.com",
        password_hash=hasher.hash(_PASSWORD),
    )
    disabled = make_user(
        email="disabled.user@example.com",
        password_hash=hasher.hash(_PASSWORD),
        status=UserStatus.DISABLED,
    )
    async with UnitOfWork(factory) as uow:
        await uow.tenants.add(tenant_a)
        await uow.tenants.add(tenant_b)
        await uow.users.add(provider)
        await uow.users.add(admin_a)
        await uow.users.add(member_a)
        await uow.users.add(disabled)
        await uow.memberships.add(
            make_membership(
                tenant_id=tenant_a.id,
                user_id=admin_a.id,
                role=TenantRole.TENANT_ADMIN,
            )
        )
        await uow.memberships.add(
            make_membership(
                tenant_id=tenant_a.id,
                user_id=member_a.id,
                role=TenantRole.MEMBER,
            )
        )
        await uow.memberships.add(
            make_membership(
                tenant_id=tenant_a.id,
                user_id=disabled.id,
                role=TenantRole.MEMBER,
            )
        )
        await uow.commit()
    return _SeededIdentity(
        tenant_a=tenant_a,
        tenant_b=tenant_b,
        admin_a=admin_a,
        member_a=member_a,
    )


async def _prepare_seed() -> _SeededIdentity:
    engine, factory = await _open_factory()
    try:
        return await _seed_identity(factory)
    finally:
        await engine.dispose()


async def _update_user_status(user_id: UUID, status: UserStatus) -> None:
    engine = create_database_engine(database_url_for(PERSISTENCE_DB))
    factory = create_session_factory(engine)
    try:
        async with UnitOfWork(factory) as uow:
            await uow.users.update_status(user_id, status)
            await uow.commit()
    finally:
        await engine.dispose()


async def _update_tenant_status(tenant_id: UUID, status: TenantStatus) -> None:
    engine = create_database_engine(database_url_for(PERSISTENCE_DB))
    factory = create_session_factory(engine)
    try:
        async with UnitOfWork(factory) as uow:
            await uow.tenants.update_status(tenant_id, status)
            await uow.commit()
    finally:
        await engine.dispose()


def test_auth_login_refresh_logout_and_rbac_matrix(auth_database: None) -> None:
    seeded = asyncio.run(_prepare_seed())
    clock = FakeClock(datetime(2026, 3, 1, 12, 0, tzinfo=UTC))
    url = database_url_for(PERSISTENCE_DB)
    application = create_app(_settings(url), clock=clock)
    _mount_matrix_routes(application)
    with TestClient(application) as client:
        provider_login = client.post(
            "/v1/auth/login",
            json={"email": "provider.admin@example.com", "password": _PASSWORD},
        )
        assert provider_login.status_code == 200
        assert "access_token" in provider_login.json()
        assert "refresh_token" not in provider_login.json()
        assert "rag_refresh" in provider_login.cookies
        assert (
            client.get(
                "/__test__/provider",
                headers=_auth_header(provider_login.json()["access_token"]),
            ).status_code
            == 200
        )

        unknown = client.post(
            "/v1/auth/login",
            json={"email": "nobody@example.com", "password": _PASSWORD},
        )
        wrong = client.post(
            "/v1/auth/login",
            json={
                "email": "admin.a@example.com",
                "password": _OTHER_PASSWORD,
                "tenant_slug": "empresa-a",
            },
        )
        assert unknown.status_code == 401
        assert wrong.status_code == 401
        assert unknown.json() == wrong.json()
        assert unknown.headers.get("www-authenticate") == "Bearer"
        assert "nobody@example.com" not in unknown.text

        admin_login = client.post(
            "/v1/auth/login",
            json={
                "email": "admin.a@example.com",
                "password": _PASSWORD,
                "tenant_slug": "empresa-a",
            },
        )
        assert admin_login.status_code == 200
        admin_token = admin_login.json()["access_token"]
        admin_refresh = admin_login.cookies["rag_refresh"]

        member_login = client.post(
            "/v1/auth/login",
            json={
                "email": "member.a@example.com",
                "password": _PASSWORD,
                "tenant_slug": "empresa-a",
            },
        )
        assert member_login.status_code == 200
        member_token = member_login.json()["access_token"]

        assert (
            client.get(
                "/v1/api-keys",
                headers=_auth_header(member_token),
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/__test__/tenant-admin",
                headers=_auth_header(member_token),
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/__test__/provider",
                headers=_auth_header(admin_token),
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/__test__/tenant-admin",
                headers=_auth_header(admin_token),
            ).status_code
            == 200
        )
        assert (
            client.get(
                "/__test__/scope-query",
                headers=_auth_header(member_token),
            ).status_code
            == 403
        )

        created = client.post(
            "/v1/api-keys",
            headers=_auth_header(admin_token),
            json={"scopes": ["query", "documents:read"]},
        )
        assert created.status_code == 201
        body = created.json()
        secret = body["secret"]
        assert secret.startswith("rag_")
        key_id = body["id"]

        listed = client.get("/v1/api-keys", headers=_auth_header(admin_token))
        assert listed.status_code == 200
        assert all("secret" not in item for item in listed.json()["items"])
        assert secret not in listed.text

        assert (
            client.get(
                "/__test__/scope-query",
                headers=_auth_header(secret),
            ).status_code
            == 200
        )

        cross = client.get(
            f"/__test__/whoami?tenant_id={seeded.tenant_b.id}",
            headers=_auth_header(secret),
        )
        assert cross.status_code == 200
        assert cross.json()["tenant_id"] == str(seeded.tenant_a.id)
        assert cross.json()["type"] == "api_key"

        body_cross = client.post(
            "/v1/api-keys",
            headers=_auth_header(secret),
            json={"scopes": ["query"], "tenant_id": str(seeded.tenant_b.id)},
        )
        assert body_cross.status_code == 403

        client.cookies.set("rag_refresh", admin_refresh, path="/v1/auth")
        refreshed = client.post("/v1/auth/refresh")
        assert refreshed.status_code == 200
        new_refresh = refreshed.cookies["rag_refresh"]
        assert new_refresh != admin_refresh
        client.cookies.set("rag_refresh", admin_refresh, path="/v1/auth")
        assert client.post("/v1/auth/refresh").status_code == 401
        client.cookies.set("rag_refresh", new_refresh, path="/v1/auth")
        assert client.post("/v1/auth/refresh").status_code == 401

        admin_login2 = client.post(
            "/v1/auth/login",
            json={
                "email": "admin.a@example.com",
                "password": _PASSWORD,
                "tenant_slug": "empresa-a",
            },
        )
        admin_token2 = admin_login2.json()["access_token"]
        revoked = client.post(
            f"/v1/api-keys/{key_id}/revoke",
            headers=_auth_header(admin_token2),
        )
        assert revoked.status_code == 200
        assert revoked.json()["status"] == "revoked"
        assert (
            client.get(
                "/__test__/scope-query",
                headers=_auth_header(secret),
            ).status_code
            == 401
        )

        client.cookies.set(
            "rag_refresh",
            admin_login2.cookies["rag_refresh"],
            path="/v1/auth",
        )
        assert client.post("/v1/auth/logout").status_code == 204
        assert client.post("/v1/auth/refresh").status_code == 401

        fresh = client.post(
            "/v1/auth/login",
            json={
                "email": "admin.a@example.com",
                "password": _PASSWORD,
                "tenant_slug": "empresa-a",
            },
        )
        fresh_token = fresh.json()["access_token"]
        clock.advance(timedelta(minutes=16))
        assert (
            client.get(
                "/__test__/tenant-admin",
                headers=_auth_header(fresh_token),
            ).status_code
            == 401
        )
        clock.advance(timedelta(minutes=-16))

        assert (
            client.post(
                "/v1/auth/login",
                json={
                    "email": "disabled.user@example.com",
                    "password": _PASSWORD,
                    "tenant_slug": "empresa-a",
                },
            ).status_code
            == 401
        )

        member_again = client.post(
            "/v1/auth/login",
            json={
                "email": "member.a@example.com",
                "password": _PASSWORD,
                "tenant_slug": "empresa-a",
            },
        )
        member_tok = member_again.json()["access_token"]
        asyncio.run(_update_user_status(seeded.member_a.id, UserStatus.DISABLED))
        assert (
            client.get(
                "/__test__/scope-query",
                headers=_auth_header(member_tok),
            ).status_code
            == 401
        )

        admin_login3 = client.post(
            "/v1/auth/login",
            json={
                "email": "admin.a@example.com",
                "password": _PASSWORD,
                "tenant_slug": "empresa-a",
            },
        )
        assert admin_login3.status_code == 200
        admin_tok3 = admin_login3.json()["access_token"]
        asyncio.run(_update_tenant_status(seeded.tenant_a.id, TenantStatus.SUSPENDED))
        assert (
            client.get(
                "/__test__/tenant-admin",
                headers=_auth_header(admin_tok3),
            ).status_code
            == 401
        )

        assert _PASSWORD not in provider_login.text
        assert secret not in listed.text
        assert admin_refresh not in refreshed.text
        assert new_refresh not in refreshed.text


def test_auth_secrets_absent_from_logs(
    auth_database: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _seed() -> None:
        engine, factory = await _open_factory()
        try:
            hasher = PasswordHasher()
            tenant = make_tenant(slug="logs-tenant", name="Logs SA")
            admin = make_user(
                email="logs.admin@example.com",
                password_hash=hasher.hash(_PASSWORD),
            )
            async with UnitOfWork(factory) as uow:
                await uow.tenants.add(tenant)
                await uow.users.add(admin)
                await uow.memberships.add(
                    make_membership(
                        tenant_id=tenant.id,
                        user_id=admin.id,
                        role=TenantRole.TENANT_ADMIN,
                    )
                )
                await uow.commit()
        finally:
            await engine.dispose()

    asyncio.run(_seed())
    url = database_url_for(PERSISTENCE_DB)
    with caplog.at_level(logging.DEBUG):
        application = create_app(_settings(url))
        with TestClient(application) as client:
            login = client.post(
                "/v1/auth/login",
                json={
                    "email": "logs.admin@example.com",
                    "password": _PASSWORD,
                    "tenant_slug": "logs-tenant",
                },
            )
            assert login.status_code == 200
            token = login.json()["access_token"]
            refresh = login.cookies["rag_refresh"]
            created = client.post(
                "/v1/api-keys",
                headers=_auth_header(token),
                json={"scopes": ["query"]},
            )
            secret = created.json()["secret"]
            client.post("/v1/auth/logout")

    joined = "\n".join(record.message for record in caplog.records)
    assert _PASSWORD not in joined
    assert refresh not in joined
    assert secret not in joined


def test_password_rehash_on_login(auth_database: None) -> None:
    weak = PasswordHasher(
        Argon2Policy(
            time_cost=1,
            memory_cost=8,
            parallelism=1,
            hash_len=16,
            salt_len=8,
        )
    )
    strong = PasswordHasher()
    user_id_box: dict[str, UUID] = {}

    async def _seed() -> None:
        engine, factory = await _open_factory()
        try:
            tenant = make_tenant(slug="rehash-co", name="Rehash SA")
            user = make_user(
                email="rehash.user@example.com",
                password_hash=weak.hash(_PASSWORD),
            )
            user_id_box["id"] = user.id
            async with UnitOfWork(factory) as uow:
                await uow.tenants.add(tenant)
                await uow.users.add(user)
                await uow.memberships.add(
                    make_membership(
                        tenant_id=tenant.id,
                        user_id=user.id,
                        role=TenantRole.MEMBER,
                    )
                )
                await uow.commit()
        finally:
            await engine.dispose()

    async def _load_hash() -> str:
        engine = create_database_engine(database_url_for(PERSISTENCE_DB))
        factory = create_session_factory(engine)
        try:
            async with UnitOfWork(factory) as uow:
                stored = await uow.users.get_by_id(user_id_box["id"])
                return stored.password_hash
        finally:
            await engine.dispose()

    asyncio.run(_seed())
    application = create_app(
        _settings(database_url_for(PERSISTENCE_DB)),
        password_hasher=strong,
    )
    with TestClient(application) as client:
        response = client.post(
            "/v1/auth/login",
            json={
                "email": "rehash.user@example.com",
                "password": _PASSWORD,
                "tenant_slug": "rehash-co",
            },
        )
        assert response.status_code == 200

    stored_hash = asyncio.run(_load_hash())
    assert strong.verify(stored_hash, _PASSWORD)
    assert not strong.needs_rehash(stored_hash)
