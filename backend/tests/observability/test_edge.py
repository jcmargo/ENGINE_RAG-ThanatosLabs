"""Bordes de la spec 18: redacción, métricas, CSRF, CORS, límites y errores públicos."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from prometheus_client import generate_latest

from app.api.edge import _AUDITED, error_payload
from app.auth.service import AuthService, LoginResult
from app.config import Environment, LogLevel, Settings
from app.domain.audit import AuditEvent
from app.domain.entities import AuthPrincipal
from app.domain.enums import AuditAction, AuditResult, PrincipalType
from app.domain.errors import AuthenticationError
from app.infrastructure.metrics import observe_http
from app.infrastructure.rate_limit import MemoryRateLimiter
from app.main import create_app
from app.observability.logging import JsonFormatter, configure_logging
from app.observability.network import is_uuid, truncate_ip
from app.observability.pseudonym import pseudonym
from app.observability.redact import clear_sentinels, redact, register_sentinel

_SECRET = "production-pseudonym-secret-32bytes!"
_SENTINEL = "CENTINELA-DOCUMENTO-NO-LOG"
_NOW = datetime(2026, 10, 5, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": Environment.TEST,
        "log_level": LogLevel.INFO,
        "api_host": "127.0.0.1",
        "api_port": 8000,
        "warm_embeddings": False,
        "pseudonym_secret": _SECRET,
        "rate_login_limit": 2,
        "rate_login_window_seconds": 60,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_redacts_secrets_and_a_document_sentinel() -> None:
    register_sentinel(_SENTINEL)
    try:
        cleaned = redact(
            "password=hunter2 api_key=abcd Bearer abc.def sk-live-secret "
            "Authorization: Basic eHh4 "
            f"extracto {_SENTINEL}"
        )
    finally:
        clear_sentinels()

    assert "hunter2" not in cleaned
    assert "abcd" not in cleaned
    assert "abc.def" not in cleaned
    assert "sk-live-secret" not in cleaned
    assert "Basic" not in cleaned
    assert _SENTINEL not in cleaned
    assert "[redacted]" in cleaned


def test_pseudonym_is_stable_and_not_the_raw_id() -> None:
    tenant = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    first = pseudonym(_SECRET, "tenant", tenant)
    second = pseudonym(_SECRET, "tenant", tenant)
    other = pseudonym(_SECRET, "principal", tenant)

    assert first == second
    assert first != other
    assert tenant not in first


def test_request_id_accepts_only_a_uuid() -> None:
    valid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert is_uuid(valid)
    assert not is_uuid("no-es-uuid")
    assert not is_uuid(f"{valid}-extra")
    assert truncate_ip("203.0.113.44") == "203.0.113.0"


class _RejectingAuth(AuthService):
    def __init__(self) -> None:
        return

    async def login(
        self,
        *,
        email: str,
        password: str,
        tenant_slug: str | None = None,
    ) -> LoginResult:
        del email, password, tenant_slug
        raise AuthenticationError

    async def resolve_bearer(self, authorization: str | None) -> AuthPrincipal:
        del authorization
        raise AuthenticationError


class _AcceptingAuth(AuthService):
    def __init__(self, result: LoginResult) -> None:
        self._result = result

    async def login(
        self,
        *,
        email: str,
        password: str,
        tenant_slug: str | None = None,
    ) -> LoginResult:
        del email, password, tenant_slug
        return self._result


def test_question_and_upload_logs_correlate_without_the_sentinel(
    caplog: pytest.LogCaptureFixture,
) -> None:
    register_sentinel(_SENTINEL)
    password = "password=hunter2"
    api_key = "api_key=super-secret-value"
    bearer = "Bearer eyJhbGciOiJIUzI1NiJ9"
    question_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    upload_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"

    try:
        application = create_app(_settings())
        with (
            TestClient(application) as client,
            caplog.at_level(logging.INFO, logger="app.access"),
        ):
            application.state.auth_service = _RejectingAuth()
            question = client.post(
                "/v1/questions",
                json={"question": f"{_SENTINEL} {password} {api_key} {bearer}"},
                headers={"X-Request-ID": question_id},
            )
            upload = client.post(
                "/v1/documents",
                files={"file": ("notas.txt", _SENTINEL.encode(), "text/plain")},
                headers={"X-Request-ID": upload_id},
            )
    finally:
        clear_sentinels()

    assert question.status_code == 401
    assert upload.status_code == 401
    assert question.headers["x-request-id"] == question_id
    assert upload.headers["x-request-id"] == upload_id
    assert question.json()["request_id"] == question_id
    assert question.json()["code"] == "authentication_failed"
    text = caplog.text
    assert question_id in text
    assert upload_id in text
    assert _SENTINEL not in text
    assert "hunter2" not in text
    assert "super-secret-value" not in text
    assert "eyJhbGciOiJIUzI1NiJ9" not in text


def test_json_formatter_redacts_a_logged_secret() -> None:
    configure_logging("INFO", service="engine-rag-api", environment="test")
    record = logging.LogRecord(
        name="app.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="password=hunter2",
        args=(),
        exc_info=None,
    )
    rendered = JsonFormatter("engine-rag-api", "test").format(record)

    assert "hunter2" not in rendered
    assert "engine-rag-api" in rendered


def test_metrics_have_low_cardinality_and_a_histogram() -> None:
    observe_http("GET", "/v1/documents/{document_id}", 200, 0.01)
    application = create_app(_settings())
    with TestClient(application, client=("127.0.0.1", 50000)) as client:
        response = client.get("/metrics")
        assert client.get("/metrics").status_code == 200

    assert response.status_code == 200
    body = response.text
    assert "engine_rag_http_request_duration_seconds_bucket" in body
    assert "tenant_id" not in body
    assert "user_id" not in body
    assert "document_id=" not in body
    assert "request_id" not in body
    with TestClient(application, client=("203.0.113.8", 50000)) as outsider:
        assert outsider.get("/metrics").status_code == 404


def test_provider_admin_can_read_metrics_from_outside() -> None:
    application = create_app(_settings())

    async def allow(_request: object) -> bool:
        return True

    application.state.metrics_admin = allow
    with TestClient(application, client=("203.0.113.9", 50000)) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    assert "Traceback" not in response.text


def test_login_failure_records_one_audit_event() -> None:
    application = create_app(_settings())

    with TestClient(application) as client:
        application.state.auth_service = _RejectingAuth()
        response = client.post(
            "/v1/auth/login",
            json={"email": "ana@example.com", "password": "password=hunter2"},
        )

    assert response.status_code == 401
    assert "hunter2" not in response.text
    events = application.state.audit_sink.events
    assert len(events) == 1
    assert events[0].action is AuditAction.AUTH_LOGIN
    assert events[0].result is AuditResult.FAILURE


def test_audit_write_failure_hides_the_success_body() -> None:
    application = create_app(_settings())

    with TestClient(application) as client:
        application.state.auth_service = _RejectingAuth()
        application.state.audit_sink.fail = True
        response = client.post(
            "/v1/auth/login",
            json={"email": "ana@example.com", "password": "secreta"},
        )

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "Traceback" not in response.text
    assert "secreta" not in response.text


def test_limiter_failure_is_closed() -> None:
    application = create_app(_settings(), rate_limiter=_UnavailableLimiter())
    with TestClient(application) as client:
        response = client.post(
            "/v1/auth/login",
            json={"email": "ana@example.com", "password": "una"},
        )
        health = client.get("/healthz")

    assert response.status_code == 503
    assert response.json()["code"] == "rate_limit_unavailable"
    assert health.status_code == 200


class _UnavailableLimiter:
    async def consume(self, _bucket_key: str, *, limit: int, window_seconds: int) -> object:
        del limit, window_seconds
        message = "postgres no responde"
        raise RuntimeError(message)


def test_audited_templates_exist_on_the_app() -> None:
    application = create_app(_settings())
    mounted: set[tuple[str, str]] = set()
    for route in application.routes:
        original = getattr(route, "original_router", route)
        for child in getattr(original, "routes", []):
            methods = getattr(child, "methods", None)
            path = getattr(child, "path", None)
            if not methods or not path:
                continue
            for method in methods:
                if method not in {"HEAD", "OPTIONS"}:
                    mounted.add((str(method), str(path)))
    missing = sorted(
        f"{method} {path}" for method, path in _AUDITED if (method, path) not in mounted
    )
    assert missing == []


def test_rate_limit_returns_retry_after() -> None:
    application = create_app(_settings(rate_login_limit=1))

    with TestClient(application) as client:
        application.state.auth_service = _RejectingAuth()
        first = client.post(
            "/v1/auth/login",
            json={"email": "ana@example.com", "password": "una"},
        )
        second = client.post(
            "/v1/auth/login",
            json={"email": "ana@example.com", "password": "dos"},
        )

    assert first.status_code == 401
    assert second.status_code == 429
    assert second.json()["code"] == "rate_limited"
    assert int(second.headers["retry-after"]) >= 1


def test_foreign_origin_has_no_cors_and_cookie_without_csrf_is_rejected() -> None:
    application = create_app(_settings())
    with TestClient(application) as client:
        foreign = client.get("/healthz", headers={"Origin": "http://evil.example"})
        allowed = client.get("/healthz", headers={"Origin": "http://localhost:5173"})
        client.cookies.set("rag_refresh", "token-de-refresh", path="/v1/auth")
        rejected = client.post("/v1/auth/refresh")

    assert "access-control-allow-origin" not in foreign.headers
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert rejected.status_code == 403
    assert rejected.json()["code"] == "csrf_failed"
    assert rejected.headers["x-content-type-options"] == "nosniff"
    assert rejected.headers["x-frame-options"] == "DENY"
    assert "strict-transport-security" not in rejected.headers


def test_hsts_is_present_only_when_https_is_confirmed() -> None:
    application = create_app(_settings(public_https=True))
    with TestClient(application) as client:
        response = client.get("/healthz")

    assert response.headers["strict-transport-security"].startswith("max-age=")


def test_unhandled_error_does_not_leak_sql_or_traceback() -> None:
    application = create_app(_settings())

    @application.get("/__boom")
    def boom() -> None:
        message = "SELECT password=hunter2 FROM users -- http://10.0.0.8/secret"
        raise RuntimeError(message)

    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/__boom")

    assert response.status_code == 500
    body = response.text
    assert "Traceback" not in body
    assert "SELECT" not in body
    assert "hunter2" not in body
    assert "10.0.0.8" not in body
    assert response.json()["code"] == "internal_error"
    assert response.json()["detail"] == "No se pudo completar la solicitud."


def test_production_rejects_weak_secret_insecure_cookie_and_wildcard() -> None:
    with pytest.raises(RuntimeError, match="POSTGRES_PASSWORD"):
        create_app(
            _settings(
                environment=Environment.PRODUCTION,
                jwt_secret="production-test-jwt-secret-32bytes!!",
                pseudonym_secret=_SECRET,
                cors_origins="https://app.example.com",
                refresh_cookie_secure=True,
            )
        )
    with pytest.raises(RuntimeError, match="cookie de refresh"):
        create_app(
            _settings(
                environment=Environment.PRODUCTION,
                jwt_secret="production-test-jwt-secret-32bytes!!",
                postgres_password="production-db-password-not-default",
                database_url=(
                    "postgresql+asyncpg://engine_rag:production-db-password-not-default"
                    "@127.0.0.1:5432/engine_rag"
                ),
                pseudonym_secret=_SECRET,
                cors_origins="https://app.example.com",
                refresh_cookie_secure=False,
            )
        )
    with pytest.raises(RuntimeError, match="CORS"):
        create_app(
            _settings(
                environment=Environment.PRODUCTION,
                jwt_secret="production-test-jwt-secret-32bytes!!",
                postgres_password="production-db-password-not-default",
                database_url=(
                    "postgresql+asyncpg://engine_rag:production-db-password-not-default"
                    "@127.0.0.1:5432/engine_rag"
                ),
                pseudonym_secret=_SECRET,
                cors_origins="*",
                refresh_cookie_secure=True,
            )
        )


def test_memory_limiter_is_independent_per_bucket() -> None:
    limiter = MemoryRateLimiter(_Clock())

    async def _run() -> None:
        first = await limiter.consume("a", limit=1, window_seconds=60)
        second = await limiter.consume("a", limit=1, window_seconds=60)
        other = await limiter.consume("b", limit=1, window_seconds=60)
        assert first.allowed
        assert not second.allowed
        assert other.allowed

    import asyncio

    asyncio.run(_run())


def test_error_payload_carries_code_detail_and_request_id() -> None:
    payload = error_payload("rate_limited", "Demasiadas solicitudes.")

    assert payload["code"] == "rate_limited"
    assert payload["detail"] == "Demasiadas solicitudes."
    assert UUID(payload["request_id"])


def test_success_login_records_the_actor() -> None:
    application = create_app(_settings())
    user_id = uuid4()
    tenant_id = uuid4()

    result = LoginResult(
        access_token="access",
        refresh_token="refresh",
        expires_at=_NOW + timedelta(minutes=15),
        principal=AuthPrincipal(
            type=PrincipalType.TENANT_ADMIN,
            user_id=user_id,
            tenant_id=tenant_id,
            role=None,
            scopes=(),
        ),
        session_id=uuid4(),
    )
    with TestClient(application) as client:
        application.state.auth_service = _AcceptingAuth(result)
        response = client.post(
            "/v1/auth/login",
            json={"email": "ana@example.com", "password": "una-clave"},
        )

    assert response.status_code == 200
    assert response.cookies.get("rag_csrf")
    event: AuditEvent = application.state.audit_sink.events[0]
    assert event.result is AuditResult.SUCCESS
    assert event.actor_id == user_id
    assert event.tenant_id == tenant_id
    assert "una-clave" not in response.text


def test_generate_latest_contains_the_http_series() -> None:
    body = generate_latest().decode()
    assert "engine_rag_http_requests_total" in body
