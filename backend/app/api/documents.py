"""Rutas de documentos. El tenant sale de `TenantContext`."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile

from app.api.deps import require_documents_read, require_documents_write
from app.api.document_schemas import DocumentListResponse, DocumentResponse
from app.documents.service import DocumentService
from app.documents.store import ReadChunk
from app.domain.document_policy import parse_document_status
from app.domain.entities import DocumentView, TenantContext
from app.domain.errors import InvalidStateError

router = APIRouter(tags=["documents"])
_CHUNK_SIZE = 64 * 1024


def get_document_service(request: Request) -> DocumentService:
    """Servicio de documentos montado en el lifespan."""
    service = getattr(request.app.state, "document_service", None)
    if not isinstance(service, DocumentService):
        message = "DocumentService no está configurado"
        raise RuntimeError(message)
    return service


@router.post("/v1/documents", response_model=DocumentResponse)
async def create_document(
    response: Response,
    context: Annotated[TenantContext, Depends(require_documents_write)],
    documents: Annotated[DocumentService, Depends(get_document_service)],
    file: Annotated[UploadFile, File()],
) -> DocumentResponse:
    """Carga un documento. Un contenido ya activo se devuelve sin copiarlo."""
    stored = await documents.create_document(
        context,
        filename=_filename(file),
        read_chunk=_reader(file),
    )
    response.status_code = 201 if stored.created else 200
    return _document_response(stored.view)


@router.get("/v1/documents", response_model=DocumentListResponse)
async def list_documents(
    context: Annotated[TenantContext, Depends(require_documents_read)],
    documents: Annotated[DocumentService, Depends(get_document_service)],
    status: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> DocumentListResponse:
    """Lista los documentos de la empresa autenticada."""
    page = await documents.list_documents(
        context,
        status=parse_document_status(status),
        cursor=cursor,
        limit=limit,
    )
    return DocumentListResponse(
        items=[_document_response(item) for item in page.items],
        next_cursor=page.next_cursor,
        total=page.total,
    )


@router.get("/v1/documents/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: UUID,
    context: Annotated[TenantContext, Depends(require_documents_read)],
    documents: Annotated[DocumentService, Depends(get_document_service)],
) -> DocumentResponse:
    """Ficha de un documento de esta empresa."""
    return _document_response(await documents.get_document(context, document_id))


@router.post("/v1/documents/{document_id}/versions", response_model=DocumentResponse)
async def add_document_version(
    document_id: UUID,
    response: Response,
    context: Annotated[TenantContext, Depends(require_documents_write)],
    documents: Annotated[DocumentService, Depends(get_document_service)],
    file: Annotated[UploadFile, File()],
) -> DocumentResponse:
    """Carga una versión nueva o devuelve el contenido activo ya almacenado."""
    stored = await documents.add_version(
        context,
        document_id,
        filename=_filename(file),
        read_chunk=_reader(file),
    )
    response.status_code = 201 if stored.created else 200
    return _document_response(stored.view)


@router.post(
    "/v1/documents/{document_id}/versions/{version_id}/retry",
    response_model=DocumentResponse,
)
async def retry_document_version(
    document_id: UUID,
    version_id: UUID,
    context: Annotated[TenantContext, Depends(require_documents_write)],
    documents: Annotated[DocumentService, Depends(get_document_service)],
) -> DocumentResponse:
    """Solicita de nuevo la ingesta de una versión fallida."""
    return _document_response(await documents.retry_version(context, document_id, version_id))


@router.delete("/v1/documents/{document_id}", response_model=DocumentResponse, status_code=202)
async def delete_document(
    document_id: UUID,
    context: Annotated[TenantContext, Depends(require_documents_write)],
    documents: Annotated[DocumentService, Depends(get_document_service)],
) -> DocumentResponse:
    """Solicita la eliminación. El documento queda en `deleting`."""
    return _document_response(await documents.request_deletion(context, document_id))


def _filename(upload: UploadFile) -> str:
    if upload.filename is None or not upload.filename.strip():
        message = "El nombre del archivo no es válido"
        raise InvalidStateError(message)
    return upload.filename


def _reader(upload: UploadFile) -> ReadChunk:
    async def read_chunk() -> bytes:
        return await upload.read(_CHUNK_SIZE)

    return read_chunk


def _document_response(view: DocumentView) -> DocumentResponse:
    return DocumentResponse(
        id=view.id,
        display_name=view.display_name,
        status=view.status.value,
        version=view.version_number,
        byte_size=view.byte_size,
        mime_type=view.mime_type,
        created_at=view.created_at,
        updated_at=view.updated_at,
        public_error_code=view.public_error_code,
        chunk_count=view.chunk_count,
        progress=view.progress,
        version_id=view.version_id,
    )
