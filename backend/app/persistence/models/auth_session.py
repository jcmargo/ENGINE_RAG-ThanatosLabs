"""Modelo ORM de sesiones de refresh."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.constraints import (
    CK_AUTH_SESSIONS_STATUS,
    FK_AUTH_SESSIONS_REPLACED_BY,
    FK_AUTH_SESSIONS_TENANT_ID,
    FK_AUTH_SESSIONS_USER_ID,
    IX_AUTH_SESSIONS_USER_ID,
    UQ_AUTH_SESSIONS_REFRESH_HASH,
)
from app.persistence.models.base import Base

_SESSION_STATUS_VALUES = ("active", "rotated", "revoked")


class AuthSessionModel(Base):
    """Fila de `auth_sessions`. Solo guarda el hash del refresh."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        UniqueConstraint("refresh_token_hash", name=UQ_AUTH_SESSIONS_REFRESH_HASH),
        CheckConstraint(
            f"status IN ({', '.join(repr(value) for value in _SESSION_STATUS_VALUES)})",
            name=CK_AUTH_SESSIONS_STATUS,
        ),
        Index(IX_AUTH_SESSIONS_USER_ID, "user_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name=FK_AUTH_SESSIONS_USER_ID, ondelete="RESTRICT"),
        nullable=False,
    )
    tenant_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", name=FK_AUTH_SESSIONS_TENANT_ID, ondelete="RESTRICT"),
        nullable=True,
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    replaced_by_session_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("auth_sessions.id", name=FK_AUTH_SESSIONS_REPLACED_BY, ondelete="RESTRICT"),
        nullable=True,
    )
