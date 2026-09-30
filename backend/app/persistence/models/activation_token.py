"""Modelo ORM de tokens de activación. Solo guarda el hash."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.constraints import (
    CK_ACTIVATION_TOKENS_STATUS,
    FK_ACTIVATION_TOKENS_TENANT_ID,
    FK_ACTIVATION_TOKENS_USER_ID,
    IX_ACTIVATION_TOKENS_TENANT_USER,
    UQ_ACTIVATION_TOKENS_HASH,
)
from app.persistence.models.base import Base

_TOKEN_STATUS_VALUES = ("pending", "consumed", "revoked")


class ActivationTokenModel(Base):
    """Fila de `activation_tokens`."""

    __tablename__ = "activation_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name=UQ_ACTIVATION_TOKENS_HASH),
        CheckConstraint(
            f"status IN ({', '.join(repr(value) for value in _TOKEN_STATUS_VALUES)})",
            name=CK_ACTIVATION_TOKENS_STATUS,
        ),
        Index(IX_ACTIVATION_TOKENS_TENANT_USER, "tenant_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", name=FK_ACTIVATION_TOKENS_TENANT_ID, ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name=FK_ACTIVATION_TOKENS_USER_ID, ondelete="RESTRICT"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
