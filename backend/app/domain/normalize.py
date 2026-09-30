"""Normalización compartida de email y slug."""

import re

from app.domain.errors import InvalidStateError

_NON_ALNUM_RUN = re.compile(r"[^a-z0-9]+")


def normalize_email(email: str) -> str:
    """Devuelve el email en forma canónica: `strip().casefold()`."""
    normalized = email.strip().casefold()
    if not normalized:
        message = "El email no puede quedar vacío tras normalizar"
        raise InvalidStateError(message)
    return normalized


def normalize_slug(slug: str) -> str:
    """Normaliza un slug a minúsculas con guiones entre segmentos alfanuméricos.

    `"Acme Corp"` y `"acme-corp"` producen el mismo resultado.
    """
    lowered = slug.strip().casefold()
    collapsed = _NON_ALNUM_RUN.sub("-", lowered).strip("-")
    if not collapsed:
        message = "El slug no puede quedar vacío tras normalizar"
        raise InvalidStateError(message)
    return collapsed
