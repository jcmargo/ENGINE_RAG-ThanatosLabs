"""Usuarios de una empresa. Todas las consultas filtran por `TenantContext`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func, select, tuple_
from sqlalchemy.exc import IntegrityError

from app.domain.entities import Membership, TenantContext, TenantMember
from app.domain.enums import MembershipStatus, TenantRole
from app.domain.errors import InvalidStateError, NotFoundError
from app.domain.pagination import PageCursor
from app.persistence.integrity import translate_integrity_error
from app.persistence.mappers import membership_to_domain, membership_to_member, membership_to_model
from app.persistence.models import MembershipModel, UserModel
from app.persistence.scoped import TenantScopedRepository

if TYPE_CHECKING:
    from app.persistence.unit_of_work import UnitOfWork

_NOT_FOUND = "Usuario no encontrado"


class SqlAlchemyTenantUserRepository(TenantScopedRepository):
    """Listado, ficha y cambios de usuarios dentro de una sola empresa."""

    def __init__(self, unit_of_work: UnitOfWork, context: TenantContext) -> None:
        super().__init__(unit_of_work, context)

    async def get_member(self, user_id: UUID) -> TenantMember:
        """Ficha del usuario en esta empresa, o ausencia si pertenece a otra."""
        model = await self._membership_for_user(user_id)
        email = await self._email_of(model.user_id)
        return membership_to_member(model, email)

    async def count_members(self, *, status: MembershipStatus | None) -> int:
        """Cuenta usuarios de esta empresa."""
        statement = (
            select(func.count())
            .select_from(MembershipModel)
            .where(MembershipModel.tenant_id == self.tenant_id)
        )
        if status is not None:
            statement = statement.where(MembershipModel.status == status.value)
        result = await self._unit.session.execute(statement)
        return int(result.scalar_one())

    async def count_active_admins(self) -> int:
        """Cuenta administradores activos de esta empresa."""
        result = await self._unit.session.execute(
            select(func.count())
            .select_from(MembershipModel)
            .where(
                MembershipModel.tenant_id == self.tenant_id,
                MembershipModel.role == TenantRole.TENANT_ADMIN.value,
                MembershipModel.status == MembershipStatus.ACTIVE.value,
            )
        )
        return int(result.scalar_one())

    async def list_members(
        self,
        *,
        status: MembershipStatus | None,
        after: PageCursor | None,
        limit: int,
    ) -> list[TenantMember]:
        """Lista usuarios de esta empresa ordenados por `(created_at, id)`."""
        statement = (
            select(MembershipModel, UserModel.email)
            .join(UserModel, UserModel.id == MembershipModel.user_id)
            .where(MembershipModel.tenant_id == self.tenant_id)
        )
        if status is not None:
            statement = statement.where(MembershipModel.status == status.value)
        if after is not None:
            statement = statement.where(
                tuple_(MembershipModel.created_at, MembershipModel.id)
                > (after.created_at, after.entity_id)
            )
        statement = statement.order_by(MembershipModel.created_at, MembershipModel.id).limit(limit)
        result = await self._unit.session.execute(statement)
        return [membership_to_member(model, email) for model, email in result.all()]

    async def add_membership(self, membership: Membership) -> Membership:
        """Persiste una membresía solo si ya pertenece a este contexto."""
        self._unit.require_open()
        if membership.tenant_id != self.tenant_id:
            message = "La membresía no pertenece al contexto de empresa"
            raise InvalidStateError(message)
        model = membership_to_model(membership)
        self._unit.session.add(model)
        try:
            await self._unit.session.flush()
        except IntegrityError as error:
            raise translate_integrity_error(error) from error
        return membership_to_domain(model)

    async def update_role(self, user_id: UUID, role: TenantRole) -> TenantMember:
        """Cambia el rol dentro de esta empresa."""
        self._unit.require_open()
        model = await self._membership_for_user(user_id)
        model.role = role.value
        model.updated_at = datetime.now(UTC)
        await self._unit.session.flush()
        email = await self._email_of(model.user_id)
        return membership_to_member(model, email)

    async def update_status(self, user_id: UUID, status: MembershipStatus) -> TenantMember:
        """Cambia el estado de la membresía dentro de esta empresa."""
        self._unit.require_open()
        model = await self._membership_for_user(user_id)
        model.status = status.value
        model.updated_at = datetime.now(UTC)
        await self._unit.session.flush()
        email = await self._email_of(model.user_id)
        return membership_to_member(model, email)

    async def _membership_for_user(self, user_id: UUID) -> MembershipModel:
        result = await self._unit.session.execute(
            select(MembershipModel).where(
                MembershipModel.tenant_id == self.tenant_id,
                MembershipModel.user_id == user_id,
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError(_NOT_FOUND)
        return model

    async def _email_of(self, user_id: UUID) -> str:
        result = await self._unit.session.execute(
            select(UserModel.email).where(UserModel.id == user_id)
        )
        email = result.scalar_one_or_none()
        if email is None:
            raise NotFoundError(_NOT_FOUND)
        return email
