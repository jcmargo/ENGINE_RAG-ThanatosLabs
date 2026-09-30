"""`/readyz` sin depender de Docker."""

import inspect

import pytest
from fastapi.testclient import TestClient

import app.infrastructure.database as database
import app.infrastructure.vector_client as vector_client
import app.main as main
from app.config import Environment, LogLevel, Settings
from app.main import create_app


def _settings() -> Settings:
    return Settings(
        environment=Environment.TEST,
        log_level=LogLevel.INFO,
        api_host="127.0.0.1",
        api_port=8000,
    )


async def _ready() -> bool:
    return True


async def _unavailable() -> bool:
    return False


def test_application_startup_does_not_create_tables() -> None:
    source = "\n".join(inspect.getsource(module) for module in (database, vector_client, main))

    assert "create_all" not in source
    assert "alembic" not in source.lower()


def test_readyz_is_ok_when_both_checks_pass() -> None:
    with TestClient(create_app(_settings(), postgres_check=_ready, qdrant_check=_ready)) as client:
        health = client.get("/healthz")
        ready = client.get("/readyz")

    assert health.status_code == 200
    assert health.content == b'{"status":"ok"}'
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ok",
        "components": {"postgresql": "ok", "qdrant": "ok"},
    }


@pytest.mark.parametrize(
    ("postgres_ok", "qdrant_ok"),
    [(False, True), (True, False), (False, False)],
)
def test_readyz_names_the_failed_component_without_secrets(
    postgres_ok: bool,
    qdrant_ok: bool,
) -> None:
    with TestClient(
        create_app(
            _settings(),
            postgres_check=_ready if postgres_ok else _unavailable,
            qdrant_check=_ready if qdrant_ok else _unavailable,
        )
    ) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "components": {
            "postgresql": "ok" if postgres_ok else "unavailable",
            "qdrant": "ok" if qdrant_ok else "unavailable",
        },
    }
    assert "engine_rag_local" not in response.text
    assert "postgresql+asyncpg" not in response.text
    assert "6333" not in response.text


def test_ready_checks_must_be_replaced_together() -> None:
    with pytest.raises(ValueError, match="juntas"):
        create_app(_settings(), postgres_check=_ready)
