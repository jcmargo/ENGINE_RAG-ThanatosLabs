"""Destino de auditoría. Memoria en tests; PostgreSQL en el piloto."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.audit import AuditEvent
from app.persistence.repositories.audit import SqlAlchemyAuditRepository
from app.persistence.unit_of_work import UnitOfWork


class AuditSink(Protocol):
    """Escribe un evento. Si falla, el llamador no esconde la acción administrativa."""

    async def write(self, event: AuditEvent) -> None:
        """Persiste exactamente este evento."""


class MemoryAuditSink:
    """Lista del proceso. Sirve para probar el conteo sin PostgreSQL."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []
        self.fail = False

    async def write(self, event: AuditEvent) -> None:
        """Guarda el evento o simula un fallo de disco."""
        if self.fail:
            message = "No se pudo escribir la auditoría"
            raise RuntimeError(message)
        self.events.append(event)


class PostgresAuditSink:
    """Una transacción propia, distinta de la del caso de uso."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def write(self, event: AuditEvent) -> None:
        """Inserta y confirma. Un fallo se propaga."""
        async with UnitOfWork(self._session_factory) as unit:
            SqlAlchemyAuditRepository(unit.session).add(event)
            await unit.commit()

    async def purge(self, *, now: datetime, retention_days: int) -> int:
        """Borra lo anterior a la ventana de retención."""
        cutoff = now - timedelta(days=retention_days)
        async with UnitOfWork(self._session_factory) as unit:
            removed = await SqlAlchemyAuditRepository(unit.session).purge_before(cutoff)
            await unit.commit()
        return removed
