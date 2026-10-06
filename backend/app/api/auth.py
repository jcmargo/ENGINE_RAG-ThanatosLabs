"""Rutas de autenticación web."""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import get_auth_service
from app.api.http import (
    clear_csrf_cookie,
    clear_refresh_cookie,
    set_csrf_cookie,
    set_refresh_cookie,
)
from app.api.schemas import AccessTokenResponse, LoginRequest
from app.auth.service import AuthService, LoginResult
from app.config import Settings
from app.domain.errors import AuthenticationError

router = APIRouter(prefix="/v1/auth", tags=["auth"])


def get_settings_from_app(request: Request) -> Settings:
    """Settings tipadas montadas en el estado de la app."""
    settings = getattr(request.app.state, "settings", None)
    if not isinstance(settings, Settings):
        message = "Settings no están configurados en la aplicación"
        raise RuntimeError(message)
    return settings


@router.post("/login", response_model=AccessTokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings_from_app)],
) -> AccessTokenResponse:
    """Login por email/contraseña y slug de empresa cuando corresponde."""
    result = await auth_service.login(
        email=body.email,
        password=body.password,
        tenant_slug=body.tenant_slug,
    )
    request.state.audit_actor_id = result.principal.user_id
    request.state.audit_tenant_id = result.principal.tenant_id
    request.state.audit_actor_kind = result.principal.type.value
    return _access_response(response, settings=settings, result=result)


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh(
    request: Request,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings_from_app)],
) -> AccessTokenResponse:
    """Rota el refresh de la cookie y emite un access nuevo."""
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    if refresh_token is None:
        raise AuthenticationError
    try:
        result = await auth_service.refresh(refresh_token)
    except AuthenticationError:
        clear_refresh_cookie(response, settings=settings)
        raise
    return _access_response(response, settings=settings, result=result)


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings_from_app)],
) -> Response:
    """Revoca la sesión y borra la cookie de refresh."""
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    await auth_service.logout(refresh_token)
    clear_refresh_cookie(response, settings=settings)
    clear_csrf_cookie(response, settings=settings)
    response.status_code = 204
    return response


def _access_response(
    response: Response,
    *,
    settings: Settings,
    result: LoginResult,
) -> AccessTokenResponse:
    max_age = timedelta(days=settings.refresh_token_days)
    set_refresh_cookie(
        response,
        settings=settings,
        refresh_token=result.refresh_token,
        max_age=max_age,
    )
    set_csrf_cookie(
        response,
        settings=settings,
        token=secrets.token_urlsafe(32),
        max_age=max_age,
    )
    return AccessTokenResponse(
        access_token=result.access_token,
        expires_in=settings.access_token_minutes * 60,
        principal_type=result.principal.type.value,
        user_id=result.principal.user_id,
        tenant_id=result.principal.tenant_id,
    )
