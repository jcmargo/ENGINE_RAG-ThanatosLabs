"""Repositorio SQLAlchemy de empresas."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func, select, tuple_
from sqlalchemy.exc import IntegrityError

from app.domain.entities import Tenant
from app.domain.enums import TenantStatus
from app.domain.errors import NotFoundError
from app.domain.normalize import normalize_slug
from app.domain.pagination import PageCursor
from app.domain.transitions import transition_tenant_status
from app.persistence.integrity import translate_integrity_error
from app.persistence.mappers import tenant_to_domain, tenant_to_model
from app.persistence.models import TenantModel

if TYPE_CHECKING:
    from app.persistence.unit_of_work import UnitOfWork


class SqlAlchemyTenantRepository:
    """Implementa el protocolo de empresas sobre la unidad de trabajo abierta."""

    def __init__(self, unit_of_work: UnitOfWork) -> None:
        self._unit = unit_of_work

    async def get_by_id(self, tenant_id: UUID) -> Tenant:
        """Obtiene una empresa por id o lanza `NotFoundError`."""
        model = await self._unit.session.get(TenantModel, tenant_id)
        if model is None:
            message = f"Empresa no encontrada: {tenant_id}"
            raise NotFoundError(message)
        return tenant_to_domain(model)

    async def get_by_slug(self, slug: str) -> Tenant:
        """Obtiene una empresa por slug normalizado o lanza `NotFoundError`."""
        normalized = normalize_slug(slug)
        result = await self._unit.session.execute(
            select(TenantModel).where(TenantModel.slug == normalized)
        )
        model = result.scalar_one_or_none()
        if model is None:
            message = f"Empresa no encontrada para slug: {normalized}"
            raise NotFoundError(message)
        return tenant_to_domain(model)

    async def exists_by_slug(self, slug: str) -> bool:
        """Indica si ya existe una empresa con ese slug normalizado."""
        normalized = normalize_slug(slug)
        result = await self._unit.session.execute(
            select(TenantModel.id).where(TenantModel.slug == normalized)
        )
        return result.scalar_one_or_none() is not None

    async def add(self, tenant: Tenant) -> Tenant:
        """Persiste una empresa nueva dentro de la unidad de trabajo abierta."""
        self._unit.require_open()
        model = tenant_to_model(tenant)
        self._unit.session.add(model)
        try:
            await self._unit.session.flush()
        except IntegrityError as error:
            raise translate_integrity_error(error) from error
        return tenant_to_domain(model)

    async def update_status(self, tenant_id: UUID, status: TenantStatus) -> Tenant:
        """Cambia el estado tras validar la transición de dominio."""
        self._unit.require_open()
        model = await self._unit.session.get(TenantModel, tenant_id)
        if model is None:
            message = f"Empresa no encontrada: {tenant_id}"
            raise NotFoundError(message)
        current = TenantStatus(model.status)
        transition_tenant_status(current, status)
        model.status = status.value
        model.updated_at = datetime.now(UTC)
        await self._unit.session.flush()
        return tenant_to_domain(model)

    async def count(self, *, status: TenantStatus | None) -> int:
        """Cuenta empresas, con filtro opcional de estado."""
        statement = select(func.count()).select_from(TenantModel)
        if status is not None:
            statement = statement.where(TenantModel.status == status.value)
        result = await self._unit.session.execute(statement)
        return int(result.scalar_one())

    async def list_page(
        self,
        *,
        status: TenantStatus | None,
        after: PageCursor | None,
        limit: int,
    ) -> list[Tenant]:
        """Lista empresas ordenadas por `(created_at, id)`."""
        statement = select(TenantModel)
        if status is not None:
            statement = statement.where(TenantModel.status == status.value)
        if after is not None:
            statement = statement.where(
                tuple_(TenantModel.created_at, TenantModel.id) > (after.created_at, after.entity_id)
            )
        statement = statement.order_by(TenantModel.created_at, TenantModel.id).limit(limit)
        result = await self._unit.session.execute(statement)
        return [tenant_to_domain(model) for model in result.scalars().all()]
