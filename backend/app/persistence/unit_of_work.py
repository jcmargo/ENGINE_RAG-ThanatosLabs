"""Unidad de trabajo async sobre la fábrica de sesiones existente."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.errors import InvalidStateError
from app.persistence.repositories.activation_tokens import SqlAlchemyActivationTokenRepository
from app.persistence.repositories.api_keys import SqlAlchemyApiKeyRepository
from app.persistence.repositories.auth_sessions import SqlAlchemyAuthSessionRepository
from app.persistence.repositories.memberships import SqlAlchemyMembershipRepository
from app.persistence.repositories.tenants import SqlAlchemyTenantRepository
from app.persistence.repositories.users import SqlAlchemyUserRepository


class UnitOfWork:
    """Abre una sesión, expone repositorios y controla commit/rollback."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._open = False
        self.tenants: SqlAlchemyTenantRepository
        self.users: SqlAlchemyUserRepository
        self.memberships: SqlAlchemyMembershipRepository
        self.auth_sessions: SqlAlchemyAuthSessionRepository
        self.api_keys: SqlAlchemyApiKeyRepository
        self.activation_tokens: SqlAlchemyActivationTokenRepository

    @property
    def session(self) -> AsyncSession:
        """Sesión activa de la unidad de trabajo."""
        if self._session is None:
            message = "La unidad de trabajo no tiene sesión abierta"
            raise InvalidStateError(message)
        return self._session

    def require_open(self) -> None:
        """Exige que la unidad siga abierta para escrituras."""
        if not self._open or self._session is None:
            message = "La unidad de trabajo está cerrada"
            raise InvalidStateError(message)

    async def __aenter__(self) -> Self:
        self._session = self._session_factory()
        self._open = True
        self.tenants = SqlAlchemyTenantRepository(self)
        self.users = SqlAlchemyUserRepository(self)
        self.memberships = SqlAlchemyMembershipRepository(self)
        self.auth_sessions = SqlAlchemyAuthSessionRepository(self)
        self.api_keys = SqlAlchemyApiKeyRepository(self)
        self.activation_tokens = SqlAlchemyActivationTokenRepository(self)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if exc_type is not None:
                await self.rollback()
        finally:
            self._open = False
            if self._session is not None:
                await self._session.close()
                self._session = None

    async def commit(self) -> None:
        """Confirma la transacción actual."""
        self.require_open()
        await self.session.commit()

    async def rollback(self) -> None:
        """Revierte la transacción actual."""
        if self._session is not None:
            await self._session.rollback()
