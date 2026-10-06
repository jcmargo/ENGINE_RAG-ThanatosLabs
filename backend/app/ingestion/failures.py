"""Clasificación de fallos y espera entre reintentos."""

import random
from dataclasses import dataclass

from qdrant_client.http.exceptions import ResponseHandlingException

from app.documents.parsers.base import (
    DocumentParseError,
    EmptyDocumentError,
    UnsupportedDocumentError,
)
from app.domain.errors import InvalidStateError, NotFoundError


class LeaseLost(Exception):
    """Otro worker recuperó el trabajo o el vencimiento ya pasó."""


class StopAfterUpsert(Exception):
    """Corta el pipeline después del upsert para simular una caída."""


class ContentHashMismatchError(Exception):
    """Los bytes leídos no son los que se guardaron al subir el archivo."""


class MissingOriginalError(Exception):
    """El original ya no está en el almacén."""


@dataclass(frozen=True)
class FailureClass:
    """Código público y si el worker debe reprogramar el trabajo."""

    permanent: bool
    code: str


def classify_failure(error: BaseException) -> FailureClass:
    """Separa errores del archivo de caídas de red o de Qdrant."""
    if isinstance(error, ContentHashMismatchError):
        return FailureClass(permanent=True, code="content_hash_mismatch")
    if isinstance(error, EmptyDocumentError):
        return FailureClass(permanent=True, code="empty_document")
    if isinstance(error, UnsupportedDocumentError):
        return FailureClass(permanent=True, code="unsupported_document")
    if isinstance(error, DocumentParseError):
        return FailureClass(permanent=True, code="document_parse_error")
    if isinstance(error, (MissingOriginalError, NotFoundError, InvalidStateError)):
        return FailureClass(permanent=True, code="missing_original")
    if isinstance(error, (TimeoutError, ConnectionError, OSError, ResponseHandlingException)):
        return FailureClass(permanent=False, code="dependency_unavailable")
    return FailureClass(permanent=False, code="dependency_unavailable")


def backoff_seconds(
    *,
    attempts: int,
    base_seconds: float,
    cap_seconds: float,
    rng: random.Random | None = None,
) -> float:
    """Backoff exponencial con jitter. `attempts` es el conteo ya incrementado."""
    exponent = max(attempts - 1, 0)
    delay = min(cap_seconds, base_seconds * (2**exponent))
    generator = random.Random() if rng is None else rng
    return float(delay / 2 + generator.uniform(0, delay / 2))
