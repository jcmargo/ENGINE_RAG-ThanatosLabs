"""Identificadores de la solicitud o del trabajo, visibles para logs y errores."""

from __future__ import annotations

from contextvars import ContextVar, Token
from uuid import UUID, uuid4

_request_id: ContextVar[str | None] = ContextVar("engine_rag_request_id", default=None)
_job_id: ContextVar[str | None] = ContextVar("engine_rag_job_id", default=None)
_principal_ref: ContextVar[str | None] = ContextVar("engine_rag_principal_ref", default=None)
_tenant_ref: ContextVar[str | None] = ContextVar("engine_rag_tenant_ref", default=None)
_route: ContextVar[str | None] = ContextVar("engine_rag_route", default=None)


def peek_request_id() -> UUID | None:
    """Id ya fijado por el middleware. Fuera de una solicitud es ausencia."""
    raw = _request_id.get()
    if raw is None:
        return None
    return UUID(raw)


def current_request_id() -> UUID:
    """Id de la solicitud. Si nadie lo fijó, genera uno y lo deja en el contexto."""
    current = peek_request_id()
    if current is not None:
        return current
    generated = uuid4()
    _request_id.set(str(generated))
    return generated


def bind_request_id(value: UUID) -> Token[str | None]:
    """Fija el id de esta solicitud hasta `reset_request_id`."""
    return _request_id.set(str(value))


def reset_request_id(token: Token[str | None]) -> None:
    """Devuelve el contexto al valor anterior."""
    _request_id.reset(token)


def bind_job(
    *,
    job_id: UUID,
    origin_request_id: UUID | None,
    tenant_ref: str | None,
) -> tuple[Token[str | None], Token[str | None], Token[str | None]]:
    """Correlaciona el worker con la carga que creó el trabajo."""
    request_token = _request_id.set(None if origin_request_id is None else str(origin_request_id))
    job_token = _job_id.set(str(job_id))
    tenant_token = _tenant_ref.set(tenant_ref)
    return request_token, job_token, tenant_token


def reset_job(
    tokens: tuple[Token[str | None], Token[str | None], Token[str | None]],
) -> None:
    """Suelta el contexto del trabajo."""
    _request_id.reset(tokens[0])
    _job_id.reset(tokens[1])
    _tenant_ref.reset(tokens[2])


def bind_actor(*, principal_ref: str | None, tenant_ref: str | None) -> None:
    """Deja los seudónimos para el log de acceso. No guarda el id en claro."""
    _principal_ref.set(principal_ref)
    _tenant_ref.set(tenant_ref)


def bind_route(route: str) -> None:
    """Ruta templada de la solicitud."""
    _route.set(route)


def snapshot() -> dict[str, str | None]:
    """Campos de correlación para una línea de log."""
    return {
        "request_id": _request_id.get(),
        "job_id": _job_id.get(),
        "principal": _principal_ref.get(),
        "tenant": _tenant_ref.get(),
        "route": _route.get(),
    }
