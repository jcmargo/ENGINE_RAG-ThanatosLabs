"""Esquemas HTTP de documentos. Sin ruta interna, hash ni `tenant_id`."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class _IgnoreExtra(BaseModel):
    """Ignora un `tenant_id` enviado por el cliente."""

    model_config = ConfigDict(extra="ignore")


class DocumentResponse(_IgnoreExtra):
    """Ficha visible de un documento."""

    id: UUID
    display_name: str
    status: str
    version: int
    byte_size: int
    mime_type: str
    created_at: datetime
    updated_at: datetime
    public_error_code: str | None
    chunk_count: int
    progress: int
    version_id: UUID


class DocumentListResponse(_IgnoreExtra):
    """Página de documentos de la empresa autenticada."""

    items: list[DocumentResponse]
    next_cursor: str | None
    total: int
