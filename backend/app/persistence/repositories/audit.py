"""Inserción y retención de auditoría. No hay lectura ni actualización."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.audit import AuditEvent
from app.persistence.models.audit_event import AuditEventModel


class SqlAlchemyAuditRepository:
    """Append-only. El borrado solo elimina filas ya vencidas."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, event: AuditEvent) -> None:
        """Inserta el evento. El commit lo hace el llamador."""
        self._session.add(
            AuditEventModel(
                id=event.id,
                tenant_id=event.tenant_id,
                actor_id=event.actor_id,
                actor_kind=event.actor_kind,
                action=event.action.value,
                resource_type=event.resource_type,
                resource_id=event.resource_id,
                result=event.result.value,
                client_ip_prefix=event.client_ip_prefix,
                created_at=event.created_at,
            )
        )

    async def purge_before(self, moment: datetime) -> int:
        """Borra eventos anteriores a `moment`. Es la retención, no una edición."""
        result = await self._session.execute(
            delete(AuditEventModel).where(AuditEventModel.created_at < moment)
        )
        deleted = getattr(result, "rowcount", 0)
        return int(deleted or 0)
