"""Dos procesos comparten el cupo y la auditoría persiste en su propia transacción."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Environment, LogLevel, Settings
from app.domain.audit import AuditEvent
from app.domain.enums import AuditAction, AuditResult
from app.infrastructure.database import create_database_engine, create_session_factory
from app.infrastructure.rate_limit import PostgresRateLimiter
from app.main import create_app
from app.observability.audit_sink import PostgresAuditSink
from app.persistence.unit_of_work import UnitOfWork
from tests.integration.db_helpers import (
    database_url_for,
    drop_and_create_database,
    drop_database,
    run_alembic,
)

pytestmark = pytest.mark.integration

OBSERVABILITY_DB = "engine_rag_observability_test"
_NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


@pytest.fixture(scope="module")
def observability_database() -> Iterator[None]:
    """Base desechable. No usa `engine_rag`."""
    asyncio.run(drop_and_create_database(OBSERVABILITY_DB))
    run_alembic("upgrade", "head", database_url=database_url_for(OBSERVABILITY_DB))
    try:
        yield
    finally:
        asyncio.run(drop_database(OBSERVABILITY_DB))


def test_two_api_processes_share_the_postgres_count(observability_database: None) -> None:
    asyncio.run(_shared_count())


def test_audit_persists_and_retention_deletes_only_old_rows(observability_database: None) -> None:
    asyncio.run(_audit_round_trip())


async def _shared_count() -> None:
    url = database_url_for(OBSERVABILITY_DB)
    engine_a = create_database_engine(url)
    engine_b = create_database_engine(url)
    limiter_a = PostgresRateLimiter(create_session_factory(engine_a), _Clock())
    limiter_b = PostgresRateLimiter(create_session_factory(engine_b), _Clock())
    settings = Settings(
        environment=Environment.TEST,
        log_level=LogLevel.INFO,
        database_url=url,
        warm_embeddings=False,
        rate_login_limit=1,
        rate_login_window_seconds=60,
    )
    app_a = create_app(settings, rate_limiter=limiter_a, clock=_Clock())
    app_b = create_app(settings, rate_limiter=limiter_b, clock=_Clock())
    try:
        with TestClient(app_a, client=("127.0.0.1", 50000)) as client_a:
            first = client_a.post(
                "/v1/auth/login",
                json={"email": "nadie@example.com", "password": "una"},
            )
        with TestClient(app_b, client=("127.0.0.1", 50001)) as client_b:
            second = client_b.post(
                "/v1/auth/login",
                json={"email": "nadie@example.com", "password": "dos"},
            )
    finally:
        await engine_a.dispose()
        await engine_b.dispose()

    assert first.status_code == 401
    assert second.status_code == 429
    assert second.headers["retry-after"]
    assert second.json()["code"] == "rate_limited"


async def _audit_round_trip() -> None:
    async with _factory() as factory:
        sink = PostgresAuditSink(factory)
        recent = _event(created_at=_NOW)
        old = _event(created_at=_NOW - timedelta(days=91))
        await sink.write(recent)
        await sink.write(old)
        removed = await sink.purge(now=_NOW, retention_days=90)
        async with UnitOfWork(factory) as unit:
            result = await unit.session.execute(text("SELECT id FROM audit_events"))
            rows = result.scalars().all()
    assert removed == 1
    assert rows == [recent.id]


def _event(*, created_at: datetime) -> AuditEvent:
    return AuditEvent(
        id=uuid4(),
        tenant_id=None,
        actor_id=None,
        actor_kind="anonymous",
        action=AuditAction.AUTH_LOGIN,
        resource_type="session",
        resource_id=None,
        result=AuditResult.FAILURE,
        client_ip_prefix="203.0.113.0",
        created_at=created_at,
    )


@asynccontextmanager
async def _factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_database_engine(database_url_for(OBSERVABILITY_DB))
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()
