"""Traducción de errores de integridad de PostgreSQL a errores de dominio."""

from sqlalchemy.exc import IntegrityError

from app.domain.errors import ConflictError, NotFoundError

_UNIQUE_VIOLATION = "23505"
_FOREIGN_KEY_VIOLATION = "23503"


def translate_integrity_error(error: IntegrityError) -> ConflictError | NotFoundError:
    """Convierte unicidad en `ConflictError` y FK ausente en `NotFoundError`."""
    sqlstate = getattr(error.orig, "sqlstate", None)
    if sqlstate == _UNIQUE_VIOLATION:
        message = "Conflicto de unicidad al persistir"
        return ConflictError(message)
    if sqlstate == _FOREIGN_KEY_VIOLATION:
        message = "Referencia inexistente al persistir"
        return NotFoundError(message)
    message = "Error de integridad al persistir"
    return ConflictError(message)
