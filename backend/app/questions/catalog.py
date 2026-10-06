"""Catálogo de documentos de una empresa para una sola pregunta."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.entities import TenantContext
from app.persistence.repositories.documents import SqlAlchemyDocumentRepository
from app.persistence.unit_of_work import UnitOfWork
from app.retrieval.contracts import DocumentReadCatalog


class SqlAlchemyCatalogOpener:
    """Abre una unidad de trabajo y el repositorio ya acotado al contexto."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @asynccontextmanager
    async def open(self, context: TenantContext) -> AsyncIterator[DocumentReadCatalog]:
        """Cierra la sesión al terminar la recuperación."""
        async with UnitOfWork(self._session_factory) as unit:
            yield SqlAlchemyDocumentRepository(unit, context)
