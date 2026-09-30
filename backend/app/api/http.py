"""Utilidades HTTP compartidas de autenticación."""

from __future__ import annotations

from datetime import timedelta

from fastapi import Response
from fastapi.responses import JSONResponse

from app.config import Environment, Settings
from app.domain.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    InvalidStateError,
    NotFoundError,
)

WWW_AUTHENTICATE = "Bearer"


def authentication_error_response(error: AuthenticationError) -> JSONResponse:
    """401 genérico con esquema Bearer."""
    return JSONResponse(
        status_code=401,
        content={"detail": error.message},
        headers={"WWW-Authenticate": WWW_AUTHENTICATE},
    )


def authorization_error_response(error: AuthorizationError) -> JSONResponse:
    """403 cuando la identidad es válida pero el permiso no alcanza."""
    return JSONResponse(status_code=403, content={"detail": error.message})


def invalid_state_error_response(error: InvalidStateError) -> JSONResponse:
    """400 para entradas de dominio inválidas (p. ej. scopes)."""
    return JSONResponse(status_code=400, content={"detail": error.message})


def not_found_error_response(error: NotFoundError) -> JSONResponse:
    """404 cuando el recurso no está en el alcance o no existe."""
    return JSONResponse(status_code=404, content={"detail": error.message})


def conflict_error_response(error: ConflictError) -> JSONResponse:
    """409 cuando la operación choca con una regla de estado."""
    return JSONResponse(status_code=409, content={"detail": error.message})


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
        secure=settings.environment is Environment.PRODUCTION,
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
        secure=settings.environment is Environment.PRODUCTION,
    )
