"""Modelo ORM de usuarios globales."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.constraints import CK_USERS_STATUS, CK_USERS_SYSTEM_ROLE, UQ_USERS_EMAIL
from app.persistence.models.base import Base

_USER_STATUS_VALUES = ("active", "disabled")
_SYSTEM_ROLE_VALUES = ("provider_admin",)


class UserModel(Base):
    """Fila de `users`."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name=UQ_USERS_EMAIL),
        CheckConstraint(
            f"status IN ({', '.join(repr(value) for value in _USER_STATUS_VALUES)})",
            name=CK_USERS_STATUS,
        ),
        CheckConstraint(
            "system_role IS NULL OR system_role IN ("
            + ", ".join(repr(value) for value in _SYSTEM_ROLE_VALUES)
            + ")",
            name=CK_USERS_SYSTEM_ROLE,
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    system_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
