"""Reglas puras de cuota, reintento y eliminación de documentos."""

from app.domain.enums import DocumentVersionStatus
from app.domain.errors import ConflictError, InvalidStateError, NotFoundError

_NOT_FOUND = "Documento no encontrado"
_DELETING = "El documento se está eliminando"
_RETRY = "La versión no se puede reintentar"
_DOCUMENT_QUOTA = "Se superó la cuota de documentos de la empresa"
_STORAGE_QUOTA = "Se superó la cuota de almacenamiento de la empresa"


def parse_document_status(value: str | None) -> DocumentVersionStatus | None:
    """Acepta solo estados de versión conocidos."""
    if value is None or not value.strip():
        return None
    try:
        return DocumentVersionStatus(value)
    except ValueError as error:
        message = "Filtro de estado desconocido"
        raise InvalidStateError(message) from error


def assert_within_quota(
    *,
    document_count: int,
    stored_bytes: int,
    incoming_bytes: int,
    max_documents: int,
    max_bytes: int,
    is_new_document: bool,
) -> None:
    """Rechaza la operación si la empresa superaría sus cupos."""
    if is_new_document and document_count >= max_documents:
        raise ConflictError(_DOCUMENT_QUOTA)
    if stored_bytes + incoming_bytes > max_bytes:
        raise ConflictError(_STORAGE_QUOTA)


def assert_accepts_new_version(status: DocumentVersionStatus) -> None:
    """Una empresa no versiona un documento eliminado o en eliminación."""
    if status is DocumentVersionStatus.DELETED:
        raise NotFoundError(_NOT_FOUND)
    if status is DocumentVersionStatus.DELETING:
        raise ConflictError(_DELETING)


def assert_retryable(status: DocumentVersionStatus) -> None:
    """Solo una versión fallida vuelve a la cola."""
    if status is not DocumentVersionStatus.FAILED:
        raise ConflictError(_RETRY)


def assert_visible(status: DocumentVersionStatus) -> None:
    """Un documento ya eliminado se responde como ausente."""
    if status is DocumentVersionStatus.DELETED:
        raise NotFoundError(_NOT_FOUND)
