"""Bordes HTTP: request id, límites, CSRF, auditoría y headers. No decide el RAG."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import Settings
from app.domain.audit import AuditEvent
from app.domain.entities import AuthPrincipal
from app.domain.enums import AuditAction, AuditResult, PrincipalType
from app.infrastructure.metrics import observe_http
from app.infrastructure.rate_limit import RateLimiter
from app.observability.audit_sink import AuditSink
from app.observability.context import (
    bind_actor,
    bind_request_id,
    bind_route,
    reset_request_id,
    snapshot,
)
from app.observability.network import ip_allowed, is_uuid, parse_cidrs, truncate_ip
from app.observability.pseudonym import pseudonym

logger = logging.getLogger("app.access")

PUBLIC_FAILURE = "No se pudo completar la solicitud."
_UUID_IN_PATH = re.compile(
    r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_COOKIE_ROUTES = frozenset({"/v1/auth/refresh", "/v1/auth/logout"})
_OPEN_PATHS = frozenset({"/healthz", "/readyz", "/metrics"})
_FAIL_CLOSED = frozenset({"login", "refresh", "questions", "uploads", "admin"})
_AUDITED: dict[tuple[str, str], tuple[AuditAction, str]] = {
    ("POST", "/v1/auth/login"): (AuditAction.AUTH_LOGIN, "session"),
    ("POST", "/v1/auth/logout"): (AuditAction.AUTH_LOGOUT, "session"),
    ("POST", "/v1/auth/activate"): (AuditAction.AUTH_ACTIVATE, "user"),
    ("POST", "/v1/provider/tenants"): (AuditAction.TENANT_CREATE, "tenant"),
    ("POST", "/v1/provider/tenants/{tenant_id}/suspend"): (AuditAction.TENANT_SUSPEND, "tenant"),
    ("POST", "/v1/provider/tenants/{tenant_id}/reactivate"): (
        AuditAction.TENANT_REACTIVATE,
        "tenant",
    ),
    ("POST", "/v1/provider/tenants/{tenant_id}/deletion"): (AuditAction.TENANT_DELETE, "tenant"),
    ("POST", "/v1/provider/tenants/{tenant_id}/deletion/confirm"): (
        AuditAction.TENANT_DELETE,
        "tenant",
    ),
    ("POST", "/v1/exports"): (AuditAction.EXPORT, "export"),
    ("POST", "/v1/users"): (AuditAction.USER_INVITE, "user"),
    ("PATCH", "/v1/users/{user_id}"): (AuditAction.USER_UPDATE, "user"),
    ("POST", "/v1/users/{user_id}/deactivate"): (AuditAction.USER_DEACTIVATE, "user"),
    ("POST", "/v1/api-keys"): (AuditAction.API_KEY_CREATE, "api_key"),
    ("POST", "/v1/api-keys/{api_key_id}/revoke"): (AuditAction.API_KEY_REVOKE, "api_key"),
    ("POST", "/v1/documents"): (AuditAction.DOCUMENT_CREATE, "document"),
    ("POST", "/v1/documents/{document_id}/versions"): (AuditAction.DOCUMENT_VERSION, "document"),
    ("POST", "/v1/documents/{document_id}/versions/{version_id}/retry"): (
        AuditAction.DOCUMENT_RETRY,
        "document",
    ),
    ("DELETE", "/v1/documents/{document_id}"): (AuditAction.DOCUMENT_DELETE, "document"),
}
_RESOURCE_KEYS = ("document_id", "user_id", "api_key_id", "tenant_id")


def security_headers(settings: Settings) -> list[tuple[bytes, bytes]]:
    """Headers de navegador. HSTS solo con HTTPS confirmado."""
    headers = [
        (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"no-referrer"),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
        (b"x-frame-options", b"DENY"),
    ]
    if settings.public_https:
        headers.append(
            (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
        )
    return headers


def error_payload(code: str, detail: str) -> dict[str, str]:
    """Cuerpo público: código, mensaje y request id. Sin traza ni SQL."""
    from app.observability.context import current_request_id

    return {"code": code, "detail": detail, "request_id": str(current_request_id())}


def template_path(path: str, route_path: str | None) -> str:
    """Ruta templada. Un id suelto no abre una serie de métricas."""
    if route_path:
        return route_path
    return _UUID_IN_PATH.sub("/{id}", path)


def rate_scope(method: str, path: str) -> str | None:
    """Ámbito del límite. Salud y métricas no entran."""
    if path in _OPEN_PATHS or method == "OPTIONS":
        return None
    if method == "POST" and path == "/v1/auth/login":
        return "login"
    if method == "POST" and path == "/v1/auth/refresh":
        return "refresh"
    if method == "POST" and path in {"/v1/questions", "/v1/questions/stream"}:
        return "questions"
    if method == "POST" and (path == "/v1/documents" or path.endswith("/versions")):
        return "uploads"
    if path.startswith("/v1/provider/tenants") or path.startswith("/v1/users"):
        return "admin"
    if path.startswith("/v1/api-keys"):
        return "admin"
    if path == "/v1/auth/activate":
        return "admin"
    if method == "DELETE" and path.startswith("/v1/documents/"):
        return "admin"
    if method == "POST" and "/versions/" in path and path.endswith("/retry"):
        return "admin"
    return None


def limit_for(settings: Settings, scope: str) -> tuple[int, int]:
    """Cupo y ventana en segundos del ámbito."""
    table = {
        "login": (settings.rate_login_limit, settings.rate_login_window_seconds),
        "refresh": (settings.rate_refresh_limit, settings.rate_refresh_window_seconds),
        "questions": (settings.rate_questions_limit, settings.rate_questions_window_seconds),
        "uploads": (settings.rate_uploads_limit, settings.rate_uploads_window_seconds),
        "admin": (settings.rate_admin_limit, settings.rate_admin_window_seconds),
    }
    return table[scope]


def bucket_for(settings: Settings, scope: str, host: str | None, authorization: str | None) -> str:
    """HMAC del ámbito, la IP truncada y el principal. El id no viaja en claro."""
    principal = "-"
    if authorization:
        principal = hashlib.sha256(authorization.encode("utf-8")).hexdigest()
    return pseudonym(
        settings.pseudonym_secret,
        "rate",
        f"{scope}\n{truncate_ip(host) or '-'}\n{principal}",
    )


def csrf_rejected(settings: Settings, scope: Scope) -> bool:
    """Cookie de refresh sin token válido, o un Origin fuera de la allowlist."""
    path = str(scope.get("path", ""))
    if path not in _COOKIE_ROUTES:
        return False
    headers = _headers(scope)
    cookie = _cookie(headers.get("cookie", ""), settings.refresh_cookie_name)
    if cookie is None:
        return False
    origin = headers.get("origin")
    if origin is not None and origin not in _origins(settings):
        return True
    expected = _cookie(headers.get("cookie", ""), settings.csrf_cookie_name)
    provided = headers.get("x-csrf-token")
    if expected is None or provided is None or len(expected) != len(provided):
        return True
    return not _same(expected, provided)


def _same(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


class EdgeMiddleware:
    """ASGI. No bufferiza el cuerpo, así el stream de preguntas sigue saliendo."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        settings = _settings(scope)
        request_id = _incoming_request_id(_headers(scope).get("x-request-id"))
        token = bind_request_id(request_id)
        started = time.perf_counter()
        status_holder: dict[str, object] = {
            "code": 500,
            "route": str(scope.get("path", "")),
            "sent": False,
        }
        try:
            blocked = await _blocked(scope, settings)
            if blocked is not None:
                status_holder["code"] = blocked[0]
                await _audit_once(scope, settings, status_holder)
                await _send_json(send, settings, request_id, blocked[0], blocked[1], blocked[2])
                status_holder["sent"] = True
                return
            if csrf_rejected(settings, scope):
                status_holder["code"] = 403
                await _audit_once(scope, settings, status_holder)
                await _send_json(
                    send,
                    settings,
                    request_id,
                    403,
                    error_payload("csrf_failed", "La solicitud no supera la defensa CSRF."),
                    (),
                )
                status_holder["sent"] = True
                return
            await self.app(
                scope,
                receive,
                _tracked_send(send, scope, settings, request_id, status_holder),
            )
        except Exception:
            logger.exception("solicitud status=error")
            status_holder["code"] = 500
            await _audit_once(scope, settings, status_holder)
            if not status_holder["sent"]:
                await _send_json(
                    send,
                    settings,
                    request_id,
                    500,
                    error_payload("internal_error", PUBLIC_FAILURE),
                    (),
                )
                status_holder["sent"] = True
        finally:
            route = str(status_holder["route"])
            bind_route(route)
            _bind_known_actor(scope, settings)
            duration_ms = (time.perf_counter() - started) * 1000
            logger.info(
                "solicitud request_id=%s result=%s route=%s",
                snapshot()["request_id"],
                status_holder["code"],
                route,
                extra={
                    "result": str(status_holder["code"]),
                    "duration_ms": round(duration_ms, 3),
                    "route": route,
                },
            )
            observe_http(
                str(scope.get("method", "GET")),
                route,
                _code(status_holder),
                duration_ms / 1000,
            )
            reset_request_id(token)


def _settings(scope: Scope) -> Settings:
    app = scope.get("app")
    settings = getattr(getattr(app, "state", None), "settings", None)
    if not isinstance(settings, Settings):
        message = "Settings no están configurados en la aplicación"
        raise RuntimeError(message)
    return settings


def _incoming_request_id(header: str | None) -> UUID:
    if header is not None and is_uuid(header.strip()):
        return UUID(header.strip())
    return uuid4()


async def _blocked(
    scope: Scope,
    settings: Settings,
) -> tuple[int, dict[str, str], tuple[tuple[bytes, bytes], ...]] | None:
    method = str(scope.get("method", "GET"))
    path = str(scope.get("path", ""))
    scope_name = rate_scope(method, path)
    if scope_name is None:
        return None
    limiter = _limiter(scope)
    limit, window = limit_for(settings, scope_name)
    host = _client_host(scope)
    authorization = _headers(scope).get("authorization")
    try:
        if limiter is None:
            raise RuntimeError("limiter")
        decision = await limiter.consume(
            bucket_for(settings, scope_name, host, authorization),
            limit=limit,
            window_seconds=window,
        )
    except Exception:
        logger.info("limite status=unavailable scope=%s", scope_name)
        if scope_name in _FAIL_CLOSED:
            return (
                503,
                error_payload(
                    "rate_limit_unavailable",
                    "No se pudo comprobar el límite de solicitudes.",
                ),
                (),
            )
        return None
    if decision.allowed:
        return None
    retry = str(decision.retry_after_seconds).encode("ascii")
    return (
        429,
        error_payload("rate_limited", "Demasiadas solicitudes."),
        ((b"retry-after", retry),),
    )


def _limiter(scope: Scope) -> RateLimiter | None:
    app = scope.get("app")
    limiter = getattr(getattr(app, "state", None), "rate_limiter", None)
    if limiter is None or not hasattr(limiter, "consume"):
        return None
    return cast(RateLimiter, limiter)


def _tracked_send(
    send: Send,
    scope: Scope,
    settings: Settings,
    request_id: UUID,
    status_holder: dict[str, object],
) -> Send:
    """Añade headers. En una ruta auditada retiene la respuesta hasta escribir el evento."""
    held_start: Message | None = None
    held_body: list[Message] = []
    streaming = False

    async def send_wrapper(message: Message) -> None:
        nonlocal held_start, streaming
        if message["type"] != "http.response.start":
            if streaming or held_start is None:
                await send(message)
                return
            held_body.append(message)
            if message.get("more_body"):
                return
            status_code = int(held_start["status"])
            status_holder["code"] = status_code
            route = template_path(str(scope.get("path", "")), _route_path(scope))
            status_holder["route"] = route
            failed = await _audit_once(scope, settings, status_holder)
            if failed:
                status_holder["code"] = 500
                await _send_json(
                    send,
                    settings,
                    request_id,
                    500,
                    error_payload("internal_error", PUBLIC_FAILURE),
                    (),
                )
                return
            raw = cast(
                "list[tuple[bytes, bytes]]",
                held_start.setdefault("headers", []),
            )
            _stamp(raw, settings, request_id)
            await send(held_start)
            status_holder["sent"] = True
            for part in held_body:
                await send(part)
            return
        status_holder["code"] = int(message["status"])
        route = template_path(str(scope.get("path", "")), _route_path(scope))
        status_holder["route"] = route
        method = str(scope.get("method", "GET"))
        if (method, route) in _AUDITED:
            held_start = message
            return
        streaming = True
        raw = cast("list[tuple[bytes, bytes]]", message.setdefault("headers", []))
        _stamp(raw, settings, request_id)
        status_holder["sent"] = True
        await send(message)

    return send_wrapper


def _stamp(headers: list[tuple[bytes, bytes]], settings: Settings, request_id: UUID) -> None:
    _append(headers, b"x-request-id", str(request_id).encode("ascii"))
    for name, value in security_headers(settings):
        _append(headers, name, value)


async def _audit_once(
    scope: Scope,
    settings: Settings,
    status_holder: dict[str, object],
) -> bool:
    """Escribe un evento como máximo. True si la escritura falló."""
    state = scope.setdefault("state", {})
    if not isinstance(state, dict) or state.get("audit_recorded"):
        return bool(state.get("audit_write_failed")) if isinstance(state, dict) else False
    route = str(status_holder.get("route") or scope.get("path", ""))
    status_holder["route"] = template_path(route, _route_path(scope) or route)
    failed = await _audit(scope, _code(status_holder), str(status_holder["route"]))
    if isinstance(state, dict):
        state["audit_recorded"] = True
        state["audit_write_failed"] = failed
    return failed


async def _audit(scope: Scope, status_code: int, route: str) -> bool:
    method = str(scope.get("method", "GET"))
    action = _AUDITED.get((method, route))
    if action is None:
        return False
    sink = _sink(scope)
    if sink is None:
        return False
    actor_id, actor_kind, tenant_id = _actor(scope)
    event = AuditEvent(
        id=uuid4(),
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_kind=actor_kind,
        action=action[0],
        resource_type=action[1],
        resource_id=_resource_id(scope),
        result=AuditResult.SUCCESS if 200 <= status_code < 300 else AuditResult.FAILURE,
        client_ip_prefix=truncate_ip(_client_host(scope)),
        created_at=_clock_now(scope),
    )
    try:
        await sink.write(event)
    except Exception:
        logger.info("auditoria status=error action=%s", action[0].value)
        return True
    return False


def _sink(scope: Scope) -> AuditSink | None:
    app = scope.get("app")
    sink = getattr(getattr(app, "state", None), "audit_sink", None)
    if sink is None or not hasattr(sink, "write"):
        return None
    return cast(AuditSink, sink)


def _code(holder: dict[str, object]) -> int:
    value = holder.get("code", 500)
    if isinstance(value, int):
        return value
    return 500


def _actor(scope: Scope) -> tuple[UUID | None, str, UUID | None]:
    state = scope.get("state")
    if not isinstance(state, dict):
        return None, "anonymous", None
    principal = state.get("principal")
    if isinstance(principal, AuthPrincipal):
        actor = principal.user_id if principal.user_id is not None else principal.api_key_id
        return actor, principal.type.value, principal.tenant_id
    actor = state.get("audit_actor_id")
    tenant = state.get("audit_tenant_id")
    kind = state.get("audit_actor_kind")
    return (
        actor if isinstance(actor, UUID) else None,
        kind if isinstance(kind, str) else "anonymous",
        tenant if isinstance(tenant, UUID) else None,
    )


def _resource_id(scope: Scope) -> str | None:
    raw = scope.get("path_params")
    if not isinstance(raw, dict):
        return None
    for key in _RESOURCE_KEYS:
        value = raw.get(key)
        if value is not None:
            text = str(value)
            if text and len(text) <= 64 and not any(character.isspace() for character in text):
                return text
    return None


def _bind_known_actor(scope: Scope, settings: Settings) -> None:
    actor_id, _, tenant_id = _actor(scope)
    bind_actor(
        principal_ref=(
            None
            if actor_id is None
            else pseudonym(settings.pseudonym_secret, "principal", str(actor_id))
        ),
        tenant_ref=(
            None
            if tenant_id is None
            else pseudonym(settings.pseudonym_secret, "tenant", str(tenant_id))
        ),
    )


def _clock_now(scope: Scope) -> datetime:
    from datetime import UTC, datetime

    app = scope.get("app")
    clock = getattr(getattr(app, "state", None), "clock", None)
    now = clock.now() if clock is not None and hasattr(clock, "now") else datetime.now(UTC)
    return now


def _route_path(scope: Scope) -> str | None:
    route = scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str):
        return path
    return None


def _client_host(scope: Scope) -> str | None:
    client = scope.get("client")
    if isinstance(client, tuple) and client and isinstance(client[0], str):
        return client[0]
    return None


def _headers(scope: Scope) -> dict[str, str]:
    raw = scope.get("headers", [])
    parsed: dict[str, str] = {}
    if not isinstance(raw, list):
        return parsed
    for item in raw:
        if not isinstance(item, tuple) or len(item) != 2:
            continue
        name, value = item
        if isinstance(name, bytes) and isinstance(value, bytes):
            parsed[name.decode("latin-1").lower()] = value.decode("latin-1")
    return parsed


def _cookie(header: str, name: str) -> str | None:
    prefix = f"{name}="
    for part in header.split(";"):
        piece = part.strip()
        if piece.startswith(prefix):
            return piece[len(prefix) :]
    return None


def _origins(settings: Settings) -> tuple[str, ...]:
    return tuple(part.strip() for part in settings.cors_origins.split(",") if part.strip())


def _append(headers: list[tuple[bytes, bytes]], name: bytes, value: bytes) -> None:
    headers.append((name, value))


async def _send_json(
    send: Send,
    settings: Settings,
    request_id: UUID,
    status: int,
    payload: dict[str, str],
    extra: tuple[tuple[bytes, bytes], ...],
) -> None:
    body = json.dumps(payload).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"x-request-id", str(request_id).encode("ascii")),
        *security_headers(settings),
        *extra,
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def metrics_visible(host: str | None, settings: Settings) -> bool:
    """True si el peer está en la red interna configurada."""
    return ip_allowed(host, parse_cidrs(settings.metrics_allow_cidrs))


def provider_admin(principal: AuthPrincipal | None) -> bool:
    """El otro camino de `/metrics`: un provider admin ya autenticado."""
    return principal is not None and principal.type is PrincipalType.PROVIDER_ADMIN
