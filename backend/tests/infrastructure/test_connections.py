"""Conexiones reales a PostgreSQL y Qdrant. Fuera de la suite por defecto."""

import asyncio
import json
import subprocess
import time
from collections.abc import Mapping

import pytest
from fastapi.testclient import TestClient
from qdrant_client.http.models import Distance, VectorParams
from sqlalchemy import text
from sqlalchemy.engine import Row

from app.config import get_settings, repository_root
from app.infrastructure.database import create_database_engine, create_session_factory
from app.infrastructure.vector_client import create_qdrant_client
from app.main import create_app

pytestmark = pytest.mark.integration

_PROBE_TABLE = "engine_rag_probe"
_PROBE_COLLECTION = "engine_rag_probe"
_PROBE_TOKEN = "engine-rag-probe"
_HEALTHY_TIMEOUT_SECONDS = 90


def _compose() -> list[str]:
    compose_file = repository_root() / "infra" / "compose.local.yml"
    return ["docker", "compose", "-f", str(compose_file)]


def _run_compose(*args: str) -> None:
    subprocess.run([*_compose(), *args], check=True)


def _service_rows() -> list[Mapping[str, object]]:
    completed = subprocess.run(
        [*_compose(), "ps", "--all", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    raw = completed.stdout.strip()
    if not raw:
        return []
    if raw.startswith("["):
        parsed: object = json.loads(raw)
        if not isinstance(parsed, list):
            return []
        return [item for item in parsed if isinstance(item, dict)]
    rows: list[Mapping[str, object]] = []
    for line in raw.splitlines():
        item: object = json.loads(line)
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _published_bindings() -> set[tuple[str, str, int]]:
    bindings: set[tuple[str, str, int]] = set()
    for row in _service_rows():
        service = row.get("Service")
        publishers = row.get("Publishers")
        if not isinstance(service, str) or not isinstance(publishers, list):
            continue
        for publisher in publishers:
            if not isinstance(publisher, dict):
                continue
            host = publisher.get("URL")
            port = publisher.get("PublishedPort")
            if not isinstance(host, str) or not host:
                continue
            if isinstance(port, bool) or not isinstance(port, int | str):
                continue
            published = int(port)
            if published <= 0:
                continue
            bindings.add((service, host, published))
    return bindings


def _wait_healthy() -> None:
    deadline = time.monotonic() + _HEALTHY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        rows = {row.get("Service"): row for row in _service_rows()}
        postgres = rows.get("postgres")
        qdrant = rows.get("qdrant")
        if (
            isinstance(postgres, Mapping)
            and isinstance(qdrant, Mapping)
            and postgres.get("Health") == "healthy"
            and qdrant.get("Health") == "healthy"
        ):
            return
        time.sleep(1)
    message = "PostgreSQL o Qdrant no llegaron a estado healthy"
    raise RuntimeError(message)


async def _public_tables() -> set[str]:
    engine = create_database_engine(get_settings().database_url)
    try:
        async with create_session_factory(engine)() as session:
            result = await session.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
            rows: list[Row[tuple[str]]] = list(result.all())
    finally:
        await engine.dispose()
    return {row[0] for row in rows}


async def _collection_names() -> set[str]:
    client = create_qdrant_client(get_settings().qdrant_url)
    try:
        listed = await client.get_collections()
    finally:
        await client.close()
    return {collection.name for collection in listed.collections}


async def _create_probe() -> None:
    engine = create_database_engine(get_settings().database_url)
    client = create_qdrant_client(get_settings().qdrant_url)
    try:
        async with create_session_factory(engine)() as session:
            await session.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS {_PROBE_TABLE} "
                    "(id integer PRIMARY KEY, token text NOT NULL)"
                )
            )
            await session.execute(
                text(
                    f"INSERT INTO {_PROBE_TABLE} (id, token) VALUES (1, :token) "
                    "ON CONFLICT (id) DO UPDATE SET token = EXCLUDED.token"
                ),
                {"token": _PROBE_TOKEN},
            )
            await session.commit()
        if await client.collection_exists(_PROBE_COLLECTION):
            await client.delete_collection(_PROBE_COLLECTION)
        await client.create_collection(
            collection_name=_PROBE_COLLECTION,
            vectors_config=VectorParams(size=4, distance=Distance.COSINE),
        )
    finally:
        await client.close()
        await engine.dispose()


async def _read_probe_token() -> str | None:
    engine = create_database_engine(get_settings().database_url)
    try:
        async with create_session_factory(engine)() as session:
            result = await session.execute(text(f"SELECT token FROM {_PROBE_TABLE} WHERE id = 1"))
            row = result.one_or_none()
    finally:
        await engine.dispose()
    if row is None:
        return None
    token = row[0]
    if not isinstance(token, str):
        return None
    return token


async def _cleanup_probe() -> None:
    engine = create_database_engine(get_settings().database_url)
    client = create_qdrant_client(get_settings().qdrant_url)
    try:
        async with create_session_factory(engine)() as session:
            await session.execute(text(f"DROP TABLE IF EXISTS {_PROBE_TABLE}"))
            await session.commit()
        if await client.collection_exists(_PROBE_COLLECTION):
            await client.delete_collection(_PROBE_COLLECTION)
    finally:
        await client.close()
        await engine.dispose()


def test_readyz_is_ok_when_both_services_are_up() -> None:
    _wait_healthy()

    with TestClient(create_app()) as client:
        ready = client.get("/readyz")
        health = client.get("/healthz")

    assert health.status_code == 200
    assert health.content == b'{"status":"ok"}'
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ok",
        "components": {"postgresql": "ok", "qdrant": "ok", "ollama": "ok"},
    }


@pytest.mark.parametrize(
    ("service", "component"),
    [("postgres", "postgresql"), ("qdrant", "qdrant")],
)
def test_readyz_reports_the_stopped_component(service: str, component: str) -> None:
    _wait_healthy()
    other = "qdrant" if component == "postgresql" else "postgresql"

    with TestClient(create_app()) as client:
        _run_compose("stop", service)
        try:
            health = client.get("/healthz")
            ready = client.get("/readyz")
        finally:
            _run_compose("start", service)
            _wait_healthy()

    assert health.status_code == 200
    assert health.content == b'{"status":"ok"}'
    assert ready.status_code == 503
    body = ready.json()
    assert body["status"] == "unavailable"
    assert body["components"][component] == "unavailable"
    assert body["components"][other] == "ok"
    assert "engine_rag_local" not in ready.text
    assert "postgresql+asyncpg" not in ready.text


def test_published_ports_bind_only_to_loopback() -> None:
    _wait_healthy()
    bindings = _published_bindings()

    assert ("postgres", "127.0.0.1", 5432) in bindings
    assert ("qdrant", "127.0.0.1", 6333) in bindings
    assert {host for _, host, _ in bindings} == {"127.0.0.1"}


def test_probe_survives_down_and_up_without_deleting_volumes() -> None:
    """`down` sin `-v` recrea los contenedores y deja el volumen intacto."""
    _wait_healthy()
    asyncio.run(_cleanup_probe())
    tables_before = asyncio.run(_public_tables())
    collections_before = asyncio.run(_collection_names())
    asyncio.run(_create_probe())
    try:
        _run_compose("down")
        _run_compose("up", "-d")
        _wait_healthy()
        assert asyncio.run(_read_probe_token()) == _PROBE_TOKEN
        assert _PROBE_COLLECTION in asyncio.run(_collection_names())
    finally:
        _run_compose("up", "-d")
        _wait_healthy()
        asyncio.run(_cleanup_probe())

    tables_after = asyncio.run(_public_tables())
    collections_after = asyncio.run(_collection_names())
    assert _PROBE_TABLE not in tables_after
    assert _PROBE_COLLECTION not in collections_after
    assert tables_after <= tables_before
    assert collections_after <= collections_before
