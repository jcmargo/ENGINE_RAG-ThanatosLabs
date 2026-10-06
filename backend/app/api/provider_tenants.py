"""Rutas globales de empresas. Exigen `provider_admin`."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import require_provider_admin
from app.api.tenancy_schemas import (
    CreateTenantRequest,
    TenantCreatedResponse,
    TenantListResponse,
    TenantResponse,
)
from app.config import Settings, resolve_file_store_path
from app.domain.entities import AuthPrincipal, Tenant
from app.infrastructure.export_files import ExportFileStore
from app.infrastructure.local_files import LocalFileStore
from app.infrastructure.restic_store import ResticRepository
from app.infrastructure.tenant_points import QdrantTenantPoints
from app.lifecycle.deletion import TenantDeletionService
from app.tenancy.service import TenantDirectoryService, parse_tenant_status

router = APIRouter(prefix="/v1/provider/tenants", tags=["provider"])


def get_tenant_directory(request: Request) -> TenantDirectoryService:
    """Servicio de empresas montado en el lifespan."""
    service = getattr(request.app.state, "tenant_directory", None)
    if not isinstance(service, TenantDirectoryService):
        message = "TenantDirectoryService no está configurado"
        raise RuntimeError(message)
    return service


@router.post("", response_model=TenantCreatedResponse, status_code=201)
async def create_tenant(
    body: CreateTenantRequest,
    _principal: Annotated[AuthPrincipal, Depends(require_provider_admin)],
    directory: Annotated[TenantDirectoryService, Depends(get_tenant_directory)],
) -> TenantCreatedResponse:
    """Crea la empresa, su primer administrador y el token de activación."""
    created = await directory.create_tenant(
        name=body.name,
        slug=body.slug,
        admin_email=body.admin_email,
    )
    return TenantCreatedResponse(
        id=created.tenant.id,
        name=created.tenant.name,
        slug=created.tenant.slug,
        status=created.tenant.status.value,
        created_at=created.tenant.created_at,
        updated_at=created.tenant.updated_at,
        admin_user_id=created.admin_user_id,
        activation_token=created.activation_token,
    )


@router.get("", response_model=TenantListResponse)
async def list_tenants(
    _principal: Annotated[AuthPrincipal, Depends(require_provider_admin)],
    directory: Annotated[TenantDirectoryService, Depends(get_tenant_directory)],
    status: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> TenantListResponse:
    """Lista empresas con paginación estable."""
    page = await directory.list_tenants(
        status=parse_tenant_status(status),
        cursor=cursor,
        limit=limit,
    )
    return TenantListResponse(
        items=[_tenant_response(item) for item in page.items],
        next_cursor=page.next_cursor,
        total=page.total,
    )


@router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant(
    tenant_id: UUID,
    _principal: Annotated[AuthPrincipal, Depends(require_provider_admin)],
    directory: Annotated[TenantDirectoryService, Depends(get_tenant_directory)],
) -> TenantResponse:
    """Ficha de una empresa. No devuelve tokens ni contenido."""
    tenant = await directory.get_tenant(tenant_id)
    return _tenant_response(tenant)


@router.post("/{tenant_id}/suspend", response_model=TenantResponse)
async def suspend_tenant(
    tenant_id: UUID,
    _principal: Annotated[AuthPrincipal, Depends(require_provider_admin)],
    directory: Annotated[TenantDirectoryService, Depends(get_tenant_directory)],
) -> TenantResponse:
    """Suspende la empresa."""
    tenant = await directory.suspend(tenant_id)
    return _tenant_response(tenant)


@router.post("/{tenant_id}/reactivate", response_model=TenantResponse)
async def reactivate_tenant(
    tenant_id: UUID,
    _principal: Annotated[AuthPrincipal, Depends(require_provider_admin)],
    directory: Annotated[TenantDirectoryService, Depends(get_tenant_directory)],
) -> TenantResponse:
    """Reactiva la empresa."""
    tenant = await directory.reactivate(tenant_id)
    return _tenant_response(tenant)


class ConfirmDeletionRequest(BaseModel):
    """El proveedor escribe el slug para confirmar el borrado."""

    confirmation: str = Field(min_length=1, max_length=255)


def _deletion_service(request: Request) -> TenantDeletionService:
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        message = "La configuración no está montada"
        raise RuntimeError(message)
    root = resolve_file_store_path(settings)
    publisher = None
    if settings.restic_repository and settings.restic_password_file is not None:
        publisher = ResticRepository(
            settings.restic_repository,
            settings.restic_password_file,
            prune_password_file=settings.restic_prune_password_file,
            protected_paths=(str(root),),
        )
    return TenantDeletionService(
        request.app.state.session_factory,
        QdrantTenantPoints(request.app.state.qdrant_client),
        LocalFileStore(root),
        ExportFileStore(root),
        pseudonym_secret=settings.pseudonym_secret,
        publisher=publisher,
        clock=request.app.state.clock,
    )


@router.post("/{tenant_id}/deletion", response_model=TenantResponse)
async def request_tenant_deletion(
    tenant_id: UUID,
    request: Request,
    _principal: Annotated[AuthPrincipal, Depends(require_provider_admin)],
) -> TenantResponse:
    """Marca la empresa como deleting y revoca el acceso."""
    tenant = await _deletion_service(request).request(tenant_id)
    return _tenant_response(tenant)


@router.post("/{tenant_id}/deletion/confirm", status_code=204)
async def confirm_tenant_deletion(
    tenant_id: UUID,
    body: ConfirmDeletionRequest,
    request: Request,
    _principal: Annotated[AuthPrincipal, Depends(require_provider_admin)],
) -> None:
    """Borra la empresa si el slug coincide. El tombstone no lleva el nombre."""
    await _deletion_service(request).confirm(tenant_id, body.confirmation)


def _tenant_response(tenant: Tenant) -> TenantResponse:
    return TenantResponse(
        id=tenant.id,
        name=tenant.name,
        slug=tenant.slug,
        status=tenant.status.value,
        created_at=tenant.created_at,
        updated_at=tenant.updated_at,
    )
