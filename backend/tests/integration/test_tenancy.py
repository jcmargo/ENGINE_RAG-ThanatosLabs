"""Gestión de empresas, activación y aislamiento contra PostgreSQL."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.deps import require_authenticated
from app.config import Environment, LogLevel, Settings
from app.domain.entities import AuthPrincipal
from app.domain.enums import SystemRole
from app.domain.pagination import encode_page_cursor
from app.infrastructure.database import create_database_engine, create_session_factory
from app.infrastructure.passwords import Argon2Policy, PasswordHasher
from app.main import create_app
from app.persistence.unit_of_work import UnitOfWork
from tests.auth.clock import FakeClock
from tests.factories.identity import make_user
from tests.integration.db_helpers import (
    PERSISTENCE_DB,
    database_url_for,
    drop_and_create_database,
    drop_database,
    run_alembic,
    truncate_identity_tables,
)
from tests.integration.isolation import assert_hidden_like_missing, assert_page_excludes_foreign

pytestmark = pytest.mark.integration

_PASSWORD = "tenant-password-not-logged"
_HASHER = PasswordHasher(
    Argon2Policy(time_cost=2, memory_cost=8192, parallelism=1, hash_len=16, salt_len=16)
)


@pytest.fixture(scope="module")
def tenancy_database() -> Iterator[None]:
    """Base desechable para empresas y usuarios."""
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


def _settings() -> Settings:
    return Settings(
        environment=Environment.TEST,
        log_level=LogLevel.INFO,
        database_url=database_url_for(PERSISTENCE_DB),
        jwt_secret="integration-test-jwt-secret-32b!!",
    )


def _app(clock: FakeClock | None = None) -> TestClient:
    application = create_app(_settings(), clock=clock, password_hasher=_HASHER)

    @application.get("/__test__/whoami")
    async def whoami(
        principal: Annotated[AuthPrincipal, Depends(require_authenticated)],
    ) -> dict[str, str | None]:
        return {
            "type": principal.type.value,
            "tenant_id": str(principal.tenant_id) if principal.tenant_id else None,
        }

    return TestClient(application)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_provider(factory: async_sessionmaker[AsyncSession]) -> None:
    provider = make_user(
        email="provider.tenancy@example.com",
        password_hash=_HASHER.hash(_PASSWORD),
        system_role=SystemRole.PROVIDER_ADMIN,
    )
    async with UnitOfWork(factory) as uow:
        await uow.users.add(provider)
        await uow.commit()


def _login(client: TestClient, email: str, *, slug: str | None = None) -> str:
    body: dict[str, str] = {"email": email, "password": _PASSWORD}
    if slug is not None:
        body["tenant_slug"] = slug
    response = client.post("/v1/auth/login", json=body)
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    assert isinstance(token, str)
    return token


def _create_tenant(
    client: TestClient, provider: str, *, name: str, slug: str, email: str
) -> dict[str, object]:
    response = client.post(
        "/v1/provider/tenants",
        headers=_auth(provider),
        json={"name": name, "slug": slug, "admin_email": email},
    )
    assert response.status_code == 201, response.text
    payload: dict[str, object] = response.json()
    return payload


def _activate(client: TestClient, token: str, password: str = _PASSWORD) -> None:
    response = client.post("/v1/auth/activate", json={"token": token, "password": password})
    assert response.status_code == 204, response.text


def test_provider_creates_tenant_and_returns_token_once(
    tenancy_database: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _prepare() -> None:
        engine, factory = await _open_factory()
        try:
            await _seed_provider(factory)
        finally:
            await engine.dispose()

    asyncio.run(_prepare())
    with caplog.at_level(logging.INFO), _app() as client:
        provider = _login(client, "provider.tenancy@example.com")
        created = _create_tenant(
            client,
            provider,
            name="Norte Ficticia",
            slug="Norte Ficticia",
            email="admin.norte@example.com",
        )
        token = created["activation_token"]
        tenant_id = created["id"]
        assert isinstance(token, str)
        assert token not in caplog.text
        assert _PASSWORD not in caplog.text
        detail = client.get(f"/v1/provider/tenants/{tenant_id}", headers=_auth(provider))
        listing = client.get("/v1/provider/tenants", headers=_auth(provider))
        assert detail.status_code == 200
        assert "activation_token" not in detail.json()
        assert listing.status_code == 200
        assert all("activation_token" not in item for item in listing.json()["items"])
        assert listing.json()["total"] == 1

    async def _hashes() -> list[str]:
        engine = create_database_engine(database_url_for(PERSISTENCE_DB))
        try:
            async with create_session_factory(engine)() as session:
                result = await session.execute(text("SELECT token_hash FROM activation_tokens"))
                return [row[0] for row in result.all()]
        finally:
            await engine.dispose()

    stored = asyncio.run(_hashes())
    assert stored
    assert token not in stored
    assert all(len(value) == 64 for value in stored)
    with _app() as client:
        _activate(client, token)
        again = client.post("/v1/auth/activate", json={"token": token, "password": _PASSWORD})
        assert again.status_code == 401
        assert again.json()["detail"] == "Token de activación inválido"
        missing = client.post(
            "/v1/auth/activate",
            json={"token": "otro-token-inexistente", "password": _PASSWORD},
        )
        assert missing.status_code == 401
        assert missing.json()["code"] == again.json()["code"]
        assert missing.json()["detail"] == again.json()["detail"]


def test_activation_token_expires(
    tenancy_database: None,
) -> None:
    clock = FakeClock(datetime(2026, 9, 29, 12, 0, tzinfo=UTC))

    async def _prepare() -> None:
        engine, factory = await _open_factory()
        try:
            await _seed_provider(factory)
        finally:
            await engine.dispose()

    asyncio.run(_prepare())
    with _app(clock) as client:
        provider = _login(client, "provider.tenancy@example.com")
        created = _create_tenant(
            client,
            provider,
            name="Sur Ficticia",
            slug="sur-ficticia",
            email="admin.sur@example.com",
        )
        token = str(created["activation_token"])
        clock.advance(timedelta(hours=73))
        expired = client.post("/v1/auth/activate", json={"token": token, "password": _PASSWORD})
        unknown = client.post(
            "/v1/auth/activate",
            json={"token": "token-desconocido", "password": _PASSWORD},
        )
        assert expired.status_code == 401
        assert expired.json()["code"] == unknown.json()["code"]
        assert expired.json()["detail"] == unknown.json()["detail"]


def test_tenant_admin_is_isolated_from_the_other_company(tenancy_database: None) -> None:
    async def _prepare() -> None:
        engine, factory = await _open_factory()
        try:
            await _seed_provider(factory)
        finally:
            await engine.dispose()

    asyncio.run(_prepare())
    with _app() as client:
        provider = _login(client, "provider.tenancy@example.com")
        created_a = _create_tenant(
            client,
            provider,
            name="Empresa A",
            slug="empresa-a",
            email="admin.a@example.com",
        )
        created_b = _create_tenant(
            client,
            provider,
            name="Empresa B",
            slug="empresa-b",
            email="admin.b@example.com",
        )
        _activate(client, str(created_a["activation_token"]))
        _activate(client, str(created_b["activation_token"]))
        admin_a = _login(client, "admin.a@example.com", slug="empresa-a")
        admin_b = _login(client, "admin.b@example.com", slug="empresa-b")
        invited = client.post(
            "/v1/users",
            headers=_auth(admin_b),
            json={"email": "miembro.b@example.com", "role": "member", "tenant_id": created_a["id"]},
        )
        assert invited.status_code == 201, invited.text
        _activate(client, str(invited.json()["activation_token"]))
        user_b = UUID(str(created_b["admin_user_id"]))
        member_b = UUID(str(invited.json()["user_id"]))
        missing_id = uuid4()

        own = client.get("/v1/users", headers=_auth(admin_a))
        foreign_filter = client.get(
            "/v1/users",
            headers=_auth(admin_a),
            params={"tenant_id": str(created_b["id"]), "status": "active"},
        )
        assert own.status_code == 200
        assert foreign_filter.status_code == 200
        own_emails = {item["email"] for item in own.json()["items"]}
        assert_page_excludes_foreign(
            emails=own_emails,
            foreign_emails={"admin.b@example.com", "miembro.b@example.com"},
            total=own.json()["total"],
            own_count=1,
        )
        assert foreign_filter.json()["total"] == own.json()["total"]
        assert {item["email"] for item in foreign_filter.json()["items"]} == own_emails

        missing = client.get(f"/v1/users/{missing_id}", headers=_auth(admin_a))
        foreign_read = client.get(f"/v1/users/{user_b}", headers=_auth(admin_a))
        foreign_member = client.get(f"/v1/users/{member_b}", headers=_auth(admin_a))
        assert_hidden_like_missing(
            status_code=foreign_read.status_code,
            body=foreign_read.json(),
            missing_body=missing.json(),
        )
        assert_hidden_like_missing(
            status_code=foreign_member.status_code,
            body=foreign_member.json(),
            missing_body=missing.json(),
        )
        foreign_role = client.patch(
            f"/v1/users/{user_b}",
            headers=_auth(admin_a),
            json={"role": "member", "tenant_id": str(created_b["id"])},
        )
        foreign_disable = client.post(
            f"/v1/users/{member_b}/deactivate",
            headers=_auth(admin_a),
            json={"tenant_id": str(created_b["id"])},
        )
        assert_hidden_like_missing(
            status_code=foreign_role.status_code,
            body=foreign_role.json(),
            missing_body=missing.json(),
        )
        assert_hidden_like_missing(
            status_code=foreign_disable.status_code,
            body=foreign_disable.json(),
            missing_body=missing.json(),
        )

        planted = client.post(
            "/v1/users",
            headers=_auth(admin_a),
            json={
                "email": "intruso.a@example.com",
                "role": "member",
                "tenant_id": str(created_b["id"]),
            },
        )
        assert planted.status_code == 201
        listed_a = client.get("/v1/users", headers=_auth(admin_a)).json()
        listed_b = client.get("/v1/users", headers=_auth(admin_b)).json()
        assert "intruso.a@example.com" in {item["email"] for item in listed_a["items"]}
        assert "intruso.a@example.com" not in {item["email"] for item in listed_b["items"]}
        assert listed_b["total"] == 2

        member_login = _login(client, "miembro.b@example.com", slug="empresa-b")
        forbidden = client.get("/v1/users", headers=_auth(member_login))
        provider_users = client.get("/v1/users", headers=_auth(provider))
        assert forbidden.status_code == 403
        assert provider_users.status_code == 403

    async def _cursor_of(user_id: UUID) -> tuple[datetime, UUID]:
        engine = create_database_engine(database_url_for(PERSISTENCE_DB))
        try:
            async with create_session_factory(engine)() as session:
                result = await session.execute(
                    text("SELECT id, created_at FROM memberships WHERE user_id = :user_id"),
                    {"user_id": user_id},
                )
                row = result.one()
        finally:
            await engine.dispose()
        membership_id = row[0]
        created_at = row[1]
        assert isinstance(membership_id, UUID)
        assert isinstance(created_at, datetime)
        return created_at, membership_id

    created_at, membership_id = asyncio.run(_cursor_of(member_b))
    with _app() as client:
        admin_a = _login(client, "admin.a@example.com", slug="empresa-a")
        paged = client.get(
            "/v1/users",
            headers=_auth(admin_a),
            params={"cursor": encode_page_cursor(created_at, membership_id)},
        )
        assert paged.status_code == 200
        emails = {item["email"] for item in paged.json()["items"]}
        assert "miembro.b@example.com" not in emails
        assert "admin.b@example.com" not in emails


def test_last_tenant_admin_cannot_be_removed(tenancy_database: None) -> None:
    async def _prepare() -> None:
        engine, factory = await _open_factory()
        try:
            await _seed_provider(factory)
        finally:
            await engine.dispose()

    asyncio.run(_prepare())
    with _app() as client:
        provider = _login(client, "provider.tenancy@example.com")
        created = _create_tenant(
            client,
            provider,
            name="Unica",
            slug="unica",
            email="unico.admin@example.com",
        )
        _activate(client, str(created["activation_token"]))
        admin = _login(client, "unico.admin@example.com", slug="unica")
        admin_id = str(created["admin_user_id"])
        demote = client.patch(
            f"/v1/users/{admin_id}",
            headers=_auth(admin),
            json={"role": "member"},
        )
        disable = client.post(f"/v1/users/{admin_id}/deactivate", headers=_auth(admin))
        assert demote.status_code == 409
        assert disable.status_code == 409
        invited = client.post(
            "/v1/users",
            headers=_auth(admin),
            json={"email": "segundo.admin@example.com", "role": "tenant_admin"},
        )
        assert invited.status_code == 201
        _activate(client, str(invited.json()["activation_token"]))
        demote_ok = client.patch(
            f"/v1/users/{admin_id}",
            headers=_auth(admin),
            json={"role": "member"},
        )
        assert demote_ok.status_code == 200
        assert demote_ok.json()["role"] == "member"


def test_suspend_blocks_credentials_until_reactivation(tenancy_database: None) -> None:
    async def _prepare() -> None:
        engine, factory = await _open_factory()
        try:
            await _seed_provider(factory)
        finally:
            await engine.dispose()

    asyncio.run(_prepare())
    with _app() as client:
        provider = _login(client, "provider.tenancy@example.com")
        created = _create_tenant(
            client,
            provider,
            name="Pausada",
            slug="pausada",
            email="admin.pausa@example.com",
        )
        tenant_id = str(created["id"])
        _activate(client, str(created["activation_token"]))
        admin = _login(client, "admin.pausa@example.com", slug="pausada")
        created_key = client.post(
            "/v1/api-keys",
            headers=_auth(admin),
            json={"scopes": ["query"]},
        )
        assert created_key.status_code == 201, created_key.text
        secret = str(created_key.json()["secret"])
        suspended = client.post(
            f"/v1/provider/tenants/{tenant_id}/suspend", headers=_auth(provider)
        )
        assert suspended.status_code == 200
        assert (
            client.post(
                "/v1/auth/login",
                json={
                    "email": "admin.pausa@example.com",
                    "password": _PASSWORD,
                    "tenant_slug": "pausada",
                },
            ).status_code
            == 401
        )
        assert client.get("/v1/users", headers=_auth(admin)).status_code == 401
        assert client.get("/__test__/whoami", headers=_auth(secret)).status_code == 401
        restored = client.post(
            f"/v1/provider/tenants/{tenant_id}/reactivate",
            headers=_auth(provider),
        )
        assert restored.status_code == 200
        assert client.get("/v1/users", headers=_auth(admin)).status_code == 200
        whoami = client.get("/__test__/whoami", headers=_auth(secret))
        assert whoami.status_code == 200
        assert whoami.json()["tenant_id"] == tenant_id
        revoked = client.post(
            f"/v1/api-keys/{created_key.json()['id']}/revoke",
            headers=_auth(admin),
        )
        assert revoked.status_code == 200
        client.post(f"/v1/provider/tenants/{tenant_id}/suspend", headers=_auth(provider))
        client.post(f"/v1/provider/tenants/{tenant_id}/reactivate", headers=_auth(provider))
        assert client.get("/__test__/whoami", headers=_auth(secret)).status_code == 401
