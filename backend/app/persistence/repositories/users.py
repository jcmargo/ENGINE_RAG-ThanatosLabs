"""Repositorio SQLAlchemy de usuarios globales."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.domain.entities import User
from app.domain.enums import SystemRole, UserStatus
from app.domain.errors import InvalidStateError, NotFoundError
from app.domain.normalize import normalize_email
from app.domain.transitions import transition_user_status
from app.persistence.integrity import translate_integrity_error
from app.persistence.mappers import user_to_domain, user_to_model
from app.persistence.models import UserModel

if TYPE_CHECKING:
    from app.persistence.unit_of_work import UnitOfWork


class SqlAlchemyUserRepository:
    """Implementa el protocolo de usuarios sobre la unidad de trabajo abierta."""

    def __init__(self, unit_of_work: UnitOfWork) -> None:
        self._unit = unit_of_work

    async def get_by_id(self, user_id: UUID) -> User:
        """Obtiene un usuario por id o lanza `NotFoundError`."""
        model = await self._unit.session.get(UserModel, user_id)
        if model is None:
            message = f"Usuario no encontrado: {user_id}"
            raise NotFoundError(message)
        return user_to_domain(model)

    async def get_by_email(self, email: str) -> User:
        """Obtiene un usuario por email normalizado o lanza `NotFoundError`."""
        normalized = normalize_email(email)
        result = await self._unit.session.execute(
            select(UserModel).where(UserModel.email == normalized)
        )
        model = result.scalar_one_or_none()
        if model is None:
            message = f"Usuario no encontrado para email: {normalized}"
            raise NotFoundError(message)
        return user_to_domain(model)

    async def exists_by_email(self, email: str) -> bool:
        """Indica si ya existe un usuario con ese email normalizado."""
        normalized = normalize_email(email)
        result = await self._unit.session.execute(
            select(UserModel.id).where(UserModel.email == normalized)
        )
        return result.scalar_one_or_none() is not None

    async def add(self, user: User) -> User:
        """Persiste un usuario nuevo dentro de la unidad de trabajo abierta."""
        self._unit.require_open()
        model = user_to_model(user)
        self._unit.session.add(model)
        try:
            await self._unit.session.flush()
        except IntegrityError as error:
            raise translate_integrity_error(error) from error
        return user_to_domain(model)

    async def exists_provider_admin(self) -> bool:
        """Indica si ya existe al menos un provider admin."""
        result = await self._unit.session.execute(
            select(UserModel.id).where(UserModel.system_role == SystemRole.PROVIDER_ADMIN.value)
        )
        return result.scalar_one_or_none() is not None

    async def update_status(self, user_id: UUID, status: UserStatus) -> User:
        """Cambia el estado tras validar la transición de dominio."""
        self._unit.require_open()
        model = await self._unit.session.get(UserModel, user_id)
        if model is None:
            message = f"Usuario no encontrado: {user_id}"
            raise NotFoundError(message)
        current = UserStatus(model.status)
        transition_user_status(current, status)
        model.status = status.value
        model.updated_at = datetime.now(UTC)
        await self._unit.session.flush()
        return user_to_domain(model)

    async def update_password_hash(self, user_id: UUID, password_hash: str) -> User:
        """Actualiza el hash opaco de contraseña."""
        self._unit.require_open()
        if not password_hash:
            message = "El hash de contraseña no puede estar vacío"
            raise InvalidStateError(message)
        model = await self._unit.session.get(UserModel, user_id)
        if model is None:
            message = f"Usuario no encontrado: {user_id}"
            raise NotFoundError(message)
        model.password_hash = password_hash
        model.updated_at = datetime.now(UTC)
        await self._unit.session.flush()
        return user_to_domain(model)
