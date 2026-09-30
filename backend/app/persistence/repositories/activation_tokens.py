"""Repositorio SQLAlchemy de tokens de activación."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.domain.entities import ActivationToken
from app.domain.enums import ActivationTokenStatus
from app.domain.errors import NotFoundError
from app.persistence.integrity import translate_integrity_error
from app.persistence.mappers import activation_token_to_domain, activation_token_to_model
from app.persistence.models import ActivationTokenModel

if TYPE_CHECKING:
    from app.persistence.unit_of_work import UnitOfWork


class SqlAlchemyActivationTokenRepository:
    """Persistencia global de tokens. La búsqueda es por hash, no por un tenant del cliente."""

    def __init__(self, unit_of_work: UnitOfWork) -> None:
        self._unit = unit_of_work

    async def add(self, token: ActivationToken) -> ActivationToken:
        """Persiste un token nuevo. Solo el hash."""
        self._unit.require_open()
        model = activation_token_to_model(token)
        self._unit.session.add(model)
        try:
            await self._unit.session.flush()
        except IntegrityError as error:
            raise translate_integrity_error(error) from error
        return activation_token_to_domain(model)

    async def get_by_hash(self, token_hash: str) -> ActivationToken:
        """Obtiene un token por hash o lanza `NotFoundError`."""
        result = await self._unit.session.execute(
            select(ActivationTokenModel).where(ActivationTokenModel.token_hash == token_hash)
        )
        model = result.scalar_one_or_none()
        if model is None:
            message = "Token de activación no encontrado"
            raise NotFoundError(message)
        return activation_token_to_domain(model)

    async def mark_consumed(self, token_id: UUID, consumed_at: datetime) -> ActivationToken:
        """Marca el token como consumido."""
        self._unit.require_open()
        model = await self._unit.session.get(ActivationTokenModel, token_id)
        if model is None:
            message = "Token de activación no encontrado"
            raise NotFoundError(message)
        model.status = ActivationTokenStatus.CONSUMED.value
        model.consumed_at = consumed_at
        model.updated_at = consumed_at
        await self._unit.session.flush()
        return activation_token_to_domain(model)

    async def revoke_pending(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        except_id: UUID,
        revoked_at: datetime,
    ) -> None:
        """Revoca los tokens pendientes del mismo usuario y empresa."""
        self._unit.require_open()
        await self._unit.session.execute(
            update(ActivationTokenModel)
            .where(
                ActivationTokenModel.tenant_id == tenant_id,
                ActivationTokenModel.user_id == user_id,
                ActivationTokenModel.status == ActivationTokenStatus.PENDING.value,
                ActivationTokenModel.id != except_id,
            )
            .values(status=ActivationTokenStatus.REVOKED.value, updated_at=revoked_at)
        )
        await self._unit.session.flush()
