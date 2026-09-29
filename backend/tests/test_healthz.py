"""Contrato HTTP de `/healthz`."""

import pytest
from fastapi.testclient import TestClient

from app.config import Environment, LogLevel, Settings
from app.main import app, create_app


def _settings(environment: Environment) -> Settings:
    return Settings(
        environment=environment,
        log_level=LogLevel.INFO,
        api_host="127.0.0.1",
        api_port=8000,
    )


@pytest.mark.parametrize("environment", list(Environment))
def test_healthz_is_alive_for_every_environment(environment: Environment) -> None:
    with TestClient(create_app(_settings(environment))) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.content == b'{"status":"ok"}'


def test_exported_asgi_app_serves_healthz() -> None:
    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.content == b'{"status":"ok"}'
