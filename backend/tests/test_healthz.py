"""Contrato HTTP de `/healthz`."""

import pytest
from fastapi.testclient import TestClient

from app.config import DEFAULT_JWT_SECRET, Environment, LogLevel, Settings
from app.main import app, create_app

_PROD_JWT_SECRET = "production-test-jwt-secret-32bytes!!"


def _settings(environment: Environment, *, jwt_secret: str | None = None) -> Settings:
    if environment is Environment.PRODUCTION:
        secret = jwt_secret if jwt_secret is not None else _PROD_JWT_SECRET
        return Settings(
            environment=environment,
            log_level=LogLevel.INFO,
            api_host="127.0.0.1",
            api_port=8000,
            jwt_secret=secret,
        )
    if jwt_secret is not None:
        return Settings(
            environment=environment,
            log_level=LogLevel.INFO,
            api_host="127.0.0.1",
            api_port=8000,
            jwt_secret=jwt_secret,
        )
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


def test_production_rejects_default_jwt_secret() -> None:
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        create_app(_settings(Environment.PRODUCTION, jwt_secret=DEFAULT_JWT_SECRET))


def test_production_rejects_short_jwt_secret() -> None:
    with pytest.raises(RuntimeError, match="32 bytes"):
        create_app(_settings(Environment.PRODUCTION, jwt_secret="too-short-secret"))
