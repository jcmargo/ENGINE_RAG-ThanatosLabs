"""Contrato HTTP de `/healthz`."""

import pytest
from fastapi.testclient import TestClient

from app.config import DEFAULT_JWT_SECRET, Environment, LogLevel, Settings
from app.main import app, create_app

_PROD_JWT_SECRET = "production-test-jwt-secret-32bytes!!"


def _settings(
    environment: Environment,
    *,
    jwt_secret: str | None = None,
    warm_embeddings: bool = False,
) -> Settings:
    if environment is Environment.PRODUCTION:
        secret = jwt_secret if jwt_secret is not None else _PROD_JWT_SECRET
        return Settings(
            environment=environment,
            log_level=LogLevel.INFO,
            api_host="127.0.0.1",
            api_port=8000,
            jwt_secret=secret,
            postgres_password="production-db-password-not-default",
            database_url=(
                "postgresql+asyncpg://engine_rag:production-db-password-not-default"
                "@127.0.0.1:5432/engine_rag"
            ),
            pseudonym_secret="production-pseudonym-secret-32bytes!",
            cors_origins="https://app.example.com",
            refresh_cookie_secure=True,
            warm_embeddings=warm_embeddings,
        )
    if jwt_secret is not None:
        return Settings(
            environment=environment,
            log_level=LogLevel.INFO,
            api_host="127.0.0.1",
            api_port=8000,
            jwt_secret=jwt_secret,
            warm_embeddings=warm_embeddings,
        )
    return Settings(
        environment=environment,
        log_level=LogLevel.INFO,
        api_host="127.0.0.1",
        api_port=8000,
        warm_embeddings=warm_embeddings,
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


def test_startup_loads_embeddings_before_serving() -> None:
    marker = object()

    def loader(settings: Settings, _session_factory: object, _client: object) -> object:
        assert settings.warm_embeddings is True
        return marker

    application = create_app(
        _settings(Environment.DEVELOPMENT, warm_embeddings=True),
        question_loader=loader,
    )
    with TestClient(application) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert application.state.question_service is marker


def test_startup_skips_embeddings_unless_warmup_is_enabled() -> None:
    calls: list[object] = []
    application = create_app(
        _settings(Environment.DEVELOPMENT),
        question_loader=lambda *_args: calls.append(True),
    )
    with TestClient(application):
        pass

    assert calls == []
    assert getattr(application.state, "question_service", None) is None


def test_production_rejects_short_jwt_secret() -> None:
    with pytest.raises(RuntimeError, match="32 bytes"):
        create_app(_settings(Environment.PRODUCTION, jwt_secret="too-short-secret"))
