"""Repositorio SQLAlchemy de membresías."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.domain.entities import Membership
from app.domain.enums import MembershipStatus, TenantRole
from app.domain.errors import NotFoundError
from app.domain.transitions import transition_membership_status
from app.persistence.integrity import translate_integrity_error
from app.persistence.mappers import membership_to_domain, membership_to_model
from app.persistence.models import MembershipModel

if TYPE_CHECKING:
    from app.persistence.unit_of_work import UnitOfWork


class SqlAlchemyMembershipRepository:
    """Implementa el protocolo de membresías sobre la unidad de trabajo abierta."""

    def __init__(self, unit_of_work: UnitOfWork) -> None:
        self._unit = unit_of_work

    async def get_by_id(self, membership_id: UUID) -> Membership:
        """Obtiene una membresía por id o lanza `NotFoundError`."""
        model = await self._unit.session.get(MembershipModel, membership_id)
        if model is None:
            message = f"Membresía no encontrada: {membership_id}"
            raise NotFoundError(message)
        return membership_to_domain(model)

    async def get_by_tenant_and_user(self, tenant_id: UUID, user_id: UUID) -> Membership:
        """Obtiene la membresía de una pareja empresa-usuario o lanza `NotFoundError`."""
        result = await self._unit.session.execute(
            select(MembershipModel).where(
                MembershipModel.tenant_id == tenant_id,
                MembershipModel.user_id == user_id,
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            message = f"Membresía no encontrada para tenant={tenant_id} user={user_id}"
            raise NotFoundError(message)
        return membership_to_domain(model)

    async def list_by_user(self, user_id: UUID) -> list[Membership]:
        """Lista las membresías de un usuario."""
        result = await self._unit.session.execute(
            select(MembershipModel).where(MembershipModel.user_id == user_id)
        )
        return [membership_to_domain(model) for model in result.scalars().all()]

    async def list_by_tenant(self, tenant_id: UUID) -> list[Membership]:
        """Lista las membresías de una empresa."""
        result = await self._unit.session.execute(
            select(MembershipModel).where(MembershipModel.tenant_id == tenant_id)
        )
        return [membership_to_domain(model) for model in result.scalars().all()]

    async def exists(self, tenant_id: UUID, user_id: UUID) -> bool:
        """Indica si ya existe la pareja empresa-usuario."""
        result = await self._unit.session.execute(
            select(MembershipModel.id).where(
                MembershipModel.tenant_id == tenant_id,
                MembershipModel.user_id == user_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def add(self, membership: Membership) -> Membership:
        """Persiste una membresía nueva dentro de la unidad de trabajo abierta."""
        self._unit.require_open()
        model = membership_to_model(membership)
        self._unit.session.add(model)
        try:
            await self._unit.session.flush()
        except IntegrityError as error:
            raise translate_integrity_error(error) from error
        return membership_to_domain(model)

    async def update_role(self, membership_id: UUID, role: TenantRole) -> Membership:
        """Cambia el rol de una membresía."""
        self._unit.require_open()
        model = await self._unit.session.get(MembershipModel, membership_id)
        if model is None:
            message = f"Membresía no encontrada: {membership_id}"
            raise NotFoundError(message)
        model.role = role.value
        model.updated_at = datetime.now(UTC)
        await self._unit.session.flush()
        return membership_to_domain(model)

    async def update_status(
        self,
        membership_id: UUID,
        status: MembershipStatus,
    ) -> Membership:
        """Cambia el estado tras validar la transición."""
        self._unit.require_open()
        model = await self._unit.session.get(MembershipModel, membership_id)
        if model is None:
            message = f"Membresía no encontrada: {membership_id}"
            raise NotFoundError(message)
        current = MembershipStatus(model.status)
        transition_membership_status(current, status)
        model.status = status.value
        model.updated_at = datetime.now(UTC)
        await self._unit.session.flush()
        return membership_to_domain(model)
