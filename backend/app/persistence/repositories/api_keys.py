"""Repositorio SQLAlchemy de API keys."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.domain.entities import ApiKey
from app.domain.enums import ApiKeyStatus
from app.domain.errors import NotFoundError
from app.persistence.integrity import translate_integrity_error
from app.persistence.mappers import api_key_to_domain, api_key_to_model
from app.persistence.models import ApiKeyModel

if TYPE_CHECKING:
    from app.persistence.unit_of_work import UnitOfWork


class SqlAlchemyApiKeyRepository:
    """Implementa el protocolo de API keys sobre la unidad de trabajo abierta."""

    def __init__(self, unit_of_work: UnitOfWork) -> None:
        self._unit = unit_of_work

    async def get_by_id(self, api_key_id: UUID) -> ApiKey:
        """Obtiene una API key por id o lanza `NotFoundError`."""
        model = await self._unit.session.get(ApiKeyModel, api_key_id)
        if model is None:
            message = f"API key no encontrada: {api_key_id}"
            raise NotFoundError(message)
        return api_key_to_domain(model)

    async def get_by_prefix(self, prefix: str) -> ApiKey:
        """Obtiene una API key por prefijo identificable o lanza `NotFoundError`."""
        result = await self._unit.session.execute(
            select(ApiKeyModel).where(ApiKeyModel.prefix == prefix)
        )
        model = result.scalar_one_or_none()
        if model is None:
            message = f"API key no encontrada para prefijo: {prefix}"
            raise NotFoundError(message)
        return api_key_to_domain(model)

    async def list_by_tenant(self, tenant_id: UUID) -> list[ApiKey]:
        """Lista las API keys de una empresa."""
        result = await self._unit.session.execute(
            select(ApiKeyModel)
            .where(ApiKeyModel.tenant_id == tenant_id)
            .order_by(ApiKeyModel.created_at.desc())
        )
        return [api_key_to_domain(model) for model in result.scalars().all()]

    async def add(self, api_key: ApiKey) -> ApiKey:
        """Persiste una API key nueva."""
        self._unit.require_open()
        model = api_key_to_model(api_key)
        self._unit.session.add(model)
        try:
            await self._unit.session.flush()
        except IntegrityError as error:
            raise translate_integrity_error(error) from error
        return api_key_to_domain(model)

    async def mark_revoked(self, api_key_id: UUID, revoked_at: datetime) -> ApiKey:
        """Marca la API key como revocada."""
        self._unit.require_open()
        model = await self._unit.session.get(ApiKeyModel, api_key_id)
        if model is None:
            message = f"API key no encontrada: {api_key_id}"
            raise NotFoundError(message)
        model.status = ApiKeyStatus.REVOKED.value
        model.revoked_at = revoked_at
        model.updated_at = datetime.now(UTC)
        await self._unit.session.flush()
        return api_key_to_domain(model)
