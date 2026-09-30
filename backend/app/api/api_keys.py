"""Rutas de API keys para tenant_admin."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_auth_service, require_tenant_admin
from app.api.schemas import (
    ApiKeyCreatedResponse,
    ApiKeyListItem,
    ApiKeyListResponse,
    ApiKeyRevokedResponse,
    CreateApiKeyRequest,
)
from app.auth.service import AuthService
from app.domain.entities import AuthPrincipal
from app.domain.errors import InvalidStateError, NotFoundError

router = APIRouter(prefix="/v1/api-keys", tags=["api-keys"])


@router.post("", response_model=ApiKeyCreatedResponse, status_code=201)
async def create_api_key(
    body: CreateApiKeyRequest,
    principal: Annotated[AuthPrincipal, Depends(require_tenant_admin)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> ApiKeyCreatedResponse:
    """Crea una API key. El secreto completo solo aparece aquí."""
    if principal.tenant_id is None:
        message = "El principal no tiene empresa"
        raise InvalidStateError(message)
    try:
        created = await auth_service.create_api_key(
            tenant_id=principal.tenant_id,
            scopes=body.scopes,
        )
    except InvalidStateError as error:
        raise HTTPException(status_code=400, detail=error.message) from error
    key = created.api_key
    return ApiKeyCreatedResponse(
        id=key.id,
        prefix=key.prefix,
        scopes=[scope.value for scope in key.scopes],
        status=key.status.value,
        created_at=key.created_at,
        secret=created.secret,
    )


@router.get("", response_model=ApiKeyListResponse)
async def list_api_keys(
    principal: Annotated[AuthPrincipal, Depends(require_tenant_admin)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> ApiKeyListResponse:
    """Lista API keys del tenant sin secretos."""
    if principal.tenant_id is None:
        message = "El principal no tiene empresa"
        raise InvalidStateError(message)
    keys = await auth_service.list_api_keys(tenant_id=principal.tenant_id)
    return ApiKeyListResponse(
        items=[
            ApiKeyListItem(
                id=key.id,
                prefix=key.prefix,
                scopes=[scope.value for scope in key.scopes],
                status=key.status.value,
                created_at=key.created_at,
                updated_at=key.updated_at,
                revoked_at=key.revoked_at,
            )
            for key in keys
        ]
    )


@router.post("/{api_key_id}/revoke", response_model=ApiKeyRevokedResponse)
async def revoke_api_key(
    api_key_id: UUID,
    principal: Annotated[AuthPrincipal, Depends(require_tenant_admin)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> ApiKeyRevokedResponse:
    """Revoca una API key del tenant autenticado."""
    if principal.tenant_id is None:
        message = "El principal no tiene empresa"
        raise InvalidStateError(message)
    try:
        revoked = await auth_service.revoke_api_key(
            tenant_id=principal.tenant_id,
            api_key_id=api_key_id,
        )
    except NotFoundError as error:
        raise HTTPException(status_code=404, detail=error.message) from error
    return ApiKeyRevokedResponse(
        id=revoked.id,
        prefix=revoked.prefix,
        status=revoked.status.value,
        revoked_at=revoked.revoked_at,
    )
