"""Utilidades HTTP compartidas de autenticación."""

from __future__ import annotations

from datetime import timedelta

from fastapi import Response
from fastapi.responses import JSONResponse

from app.api.edge import PUBLIC_FAILURE, error_payload
from app.config import Environment, Settings
from app.domain.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    InvalidStateError,
    NotFoundError,
    PayloadTooLargeError,
)

WWW_AUTHENTICATE = "Bearer"


def authentication_error_response(error: AuthenticationError) -> JSONResponse:
    """401 genérico con esquema Bearer."""
    return JSONResponse(
        status_code=401,
        content=error_payload(error.code, error.message),
        headers={"WWW-Authenticate": WWW_AUTHENTICATE},
    )


def authorization_error_response(error: AuthorizationError) -> JSONResponse:
    """403 cuando la identidad es válida pero el permiso no alcanza."""
    return JSONResponse(status_code=403, content=error_payload(error.code, error.message))


def invalid_state_error_response(error: InvalidStateError) -> JSONResponse:
    """400 para entradas de dominio inválidas (p. ej. scopes)."""
    return JSONResponse(status_code=400, content=error_payload(error.code, error.message))


def not_found_error_response(error: NotFoundError) -> JSONResponse:
    """404 cuando el recurso no está en el alcance o no existe."""
    return JSONResponse(status_code=404, content=error_payload(error.code, error.message))


def conflict_error_response(error: ConflictError) -> JSONResponse:
    """409 cuando la operación choca con una regla de estado."""
    return JSONResponse(status_code=409, content=error_payload(error.code, error.message))


def payload_too_large_response(error: PayloadTooLargeError) -> JSONResponse:
    """413 cuando el archivo supera el límite durante la recepción."""
    return JSONResponse(status_code=413, content=error_payload(error.code, error.message))


def unexpected_error_response() -> JSONResponse:
    """5xx público. El traceback se queda en el log redactado."""
    return JSONResponse(
        status_code=500,
        content=error_payload("internal_error", PUBLIC_FAILURE),
    )


def _cookie_secure(settings: Settings) -> bool:
    return settings.refresh_cookie_secure or settings.environment is Environment.PRODUCTION


def set_csrf_cookie(
    response: Response, *, settings: Settings, token: str, max_age: timedelta
) -> None:
    """Cookie legible para el doble envío. No es HttpOnly: el cliente la copia al header."""
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=token,
        httponly=False,
        samesite="lax",
        secure=_cookie_secure(settings),
        max_age=int(max_age.total_seconds()),
        path="/",
    )


def clear_csrf_cookie(response: Response, *, settings: Settings) -> None:
    """Borra la cookie de CSRF."""
    response.delete_cookie(
        key=settings.csrf_cookie_name,
        path="/",
        samesite="lax",
        secure=_cookie_secure(settings),
    )


def set_refresh_cookie(
    response: Response,
    *,
    settings: Settings,
    refresh_token: str,
    max_age: timedelta,
) -> None:
    """Cookie HttpOnly SameSite=Lax; Secure solo en producción."""
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_token,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(settings),
        max_age=int(max_age.total_seconds()),
        path="/v1/auth",
    )


def clear_refresh_cookie(response: Response, *, settings: Settings) -> None:
    """Borra la cookie de refresh."""
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path="/v1/auth",
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(settings),
    )
