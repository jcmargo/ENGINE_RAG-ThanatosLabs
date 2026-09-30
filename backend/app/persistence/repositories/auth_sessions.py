"""Repositorio SQLAlchemy de sesiones de refresh."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.domain.entities import AuthSession
from app.domain.enums import AuthSessionStatus
from app.domain.errors import NotFoundError
from app.persistence.integrity import translate_integrity_error
from app.persistence.mappers import auth_session_to_domain, auth_session_to_model
from app.persistence.models import AuthSessionModel

if TYPE_CHECKING:
    from app.persistence.unit_of_work import UnitOfWork


class SqlAlchemyAuthSessionRepository:
    """Implementa el protocolo de sesiones sobre la unidad de trabajo abierta."""

    def __init__(self, unit_of_work: UnitOfWork) -> None:
        self._unit = unit_of_work

    async def get_by_id(self, session_id: UUID) -> AuthSession:
        """Obtiene una sesión por id o lanza `NotFoundError`."""
        model = await self._unit.session.get(AuthSessionModel, session_id)
        if model is None:
            message = f"Sesión no encontrada: {session_id}"
            raise NotFoundError(message)
        return auth_session_to_domain(model)

    async def get_by_refresh_token_hash(self, refresh_token_hash: str) -> AuthSession:
        """Obtiene una sesión por hash de refresh o lanza `NotFoundError`."""
        result = await self._unit.session.execute(
            select(AuthSessionModel).where(
                AuthSessionModel.refresh_token_hash == refresh_token_hash
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            message = "Sesión no encontrada para el refresh presentado"
            raise NotFoundError(message)
        return auth_session_to_domain(model)

    async def add(self, session: AuthSession) -> AuthSession:
        """Persiste una sesión nueva."""
        self._unit.require_open()
        model = auth_session_to_model(session)
        self._unit.session.add(model)
        try:
            await self._unit.session.flush()
        except IntegrityError as error:
            raise translate_integrity_error(error) from error
        return auth_session_to_domain(model)

    async def mark_rotated(
        self,
        session_id: UUID,
        *,
        replaced_by_session_id: UUID,
    ) -> AuthSession:
        """Marca la sesión como rotada y enlaza la sucesora."""
        self._unit.require_open()
        model = await self._unit.session.get(AuthSessionModel, session_id)
        if model is None:
            message = f"Sesión no encontrada: {session_id}"
            raise NotFoundError(message)
        model.status = AuthSessionStatus.ROTATED.value
        model.replaced_by_session_id = replaced_by_session_id
        model.updated_at = datetime.now(UTC)
        await self._unit.session.flush()
        return auth_session_to_domain(model)

    async def mark_revoked(self, session_id: UUID) -> AuthSession:
        """Marca la sesión como revocada."""
        self._unit.require_open()
        model = await self._unit.session.get(AuthSessionModel, session_id)
        if model is None:
            message = f"Sesión no encontrada: {session_id}"
            raise NotFoundError(message)
        model.status = AuthSessionStatus.REVOKED.value
        model.updated_at = datetime.now(UTC)
        await self._unit.session.flush()
        return auth_session_to_domain(model)
