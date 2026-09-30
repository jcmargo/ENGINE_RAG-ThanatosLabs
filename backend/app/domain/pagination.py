"""Cursor opaco `(created_at, id)` para listados estables."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.entities import _require_utc
from app.domain.errors import InvalidStateError


@dataclass(frozen=True)
class PageCursor:
    """Posición exclusiva a partir de la cual continúa una página."""

    created_at: datetime
    entity_id: UUID


def encode_page_cursor(created_at: datetime, entity_id: UUID) -> str:
    """Codifica el cursor sin incluir el tenant."""
    _require_utc(created_at, "created_at")
    raw = f"{created_at.isoformat()}|{entity_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_page_cursor(value: str) -> PageCursor:
    """Decodifica un cursor o lanza `InvalidStateError` si no es válido."""
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        stamp, separator, entity = decoded.partition("|")
        if separator != "|" or not stamp or not entity:
            raise ValueError
        created_at = datetime.fromisoformat(stamp)
        entity_id = UUID(entity)
    except (ValueError, UnicodeError) as error:
        message = "Cursor de paginación inválido"
        raise InvalidStateError(message) from error
    _require_utc(created_at, "cursor")
    return PageCursor(created_at=created_at, entity_id=entity_id)
