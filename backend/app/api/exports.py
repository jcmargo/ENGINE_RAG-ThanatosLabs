"""Exportación cifrada del administrador de la empresa."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import Response

from app.api.deps import require_tenant_context
from app.config import Settings, resolve_file_store_path
from app.domain.entities import TenantContext
from app.domain.errors import NotFoundError
from app.infrastructure.export_files import ExportFileStore
from app.infrastructure.local_files import LocalFileStore
from app.lifecycle.export_service import ExportService

router = APIRouter(prefix="/v1/exports", tags=["exports"])


def _service(request: Request) -> ExportService:
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        message = "La configuración no está montada"
        raise RuntimeError(message)
    root = resolve_file_store_path(settings)
    store = getattr(request.app.state, "file_store", None)
    files = store if isinstance(store, LocalFileStore) else LocalFileStore(root)
    return ExportService(
        request.app.state.session_factory,
        files,
        ExportFileStore(root),
        ttl_hours=settings.export_ttl_hours,
        public_config=_public_config(settings),
        clock=request.app.state.clock,
    )


@router.post("", status_code=201)
async def create_export(
    context: Annotated[TenantContext, Depends(require_tenant_context)],
    request: Request,
) -> dict[str, str]:
    """Crea el paquete y devuelve el token una sola vez."""
    created = await _service(request).create(context)
    return {
        "id": str(created.package_id),
        "token": created.token,
        "expires_at": created.expires_at.isoformat(),
    }


@router.get("/{package_id}")
async def download_export(
    package_id: UUID,
    request: Request,
    context: Annotated[TenantContext, Depends(require_tenant_context)],
    export_token: Annotated[str | None, Header(alias="X-Export-Token")] = None,
) -> Response:
    """Descifra el paquete. El token no viaja en la query."""
    if export_token is None:
        raise NotFoundError("La exportación no existe")
    payload = await _service(request).download(context, package_id, export_token)
    return Response(content=payload, media_type="application/zip")


def _public_config(settings: Settings) -> dict[str, object]:
    return {
        "audit_retention_days": settings.audit_retention_days,
        "conversation_retention_days": settings.conversation_retention_days,
        "embedding_model": settings.embedding_model,
        "index_revision": settings.index_revision,
        "trace_retention_days": settings.trace_retention_days,
        "vector_alias": settings.vector_alias,
    }
