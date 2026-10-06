"""Evento de auditoría. Solo metadatos: nunca contenido ni secretos."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from app.domain.enums import AuditAction, AuditResult
from app.domain.errors import InvalidStateError

_RESOURCE_ID_MAX = 64


@dataclass(frozen=True)
class AuditEvent:
    """Una acción crítica. El texto del documento o la contraseña no entran aquí."""

    id: UUID
    tenant_id: UUID | None
    actor_id: UUID | None
    actor_kind: str
    action: AuditAction
    resource_type: str
    resource_id: str | None
    result: AuditResult
    client_ip_prefix: str | None
    created_at: datetime

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() != timedelta(0):
            message = "created_at de auditoría debe ser UTC"
            raise InvalidStateError(message)
        if (
            not self.actor_kind
            or len(self.actor_kind) > 32
            or any(character.isspace() for character in self.actor_kind)
        ):
            message = "El tipo de actor de auditoría no es válido"
            raise InvalidStateError(message)
        if (
            not self.resource_type
            or len(self.resource_type) > 64
            or any(character.isspace() for character in self.resource_type)
        ):
            message = "El tipo de recurso de auditoría no es válido"
            raise InvalidStateError(message)
        if self.resource_id is not None and (
            not self.resource_id
            or len(self.resource_id) > _RESOURCE_ID_MAX
            or any(character.isspace() for character in self.resource_id)
        ):
            message = "El recurso de auditoría no es válido"
            raise InvalidStateError(message)
        if self.client_ip_prefix is not None and (
            not self.client_ip_prefix or len(self.client_ip_prefix) > 64
        ):
            message = "La IP de auditoría no es válida"
            raise InvalidStateError(message)
