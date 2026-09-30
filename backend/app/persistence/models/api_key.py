"""Modelo ORM de API keys."""

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.constraints import (
    CK_API_KEYS_STATUS,
    FK_API_KEYS_TENANT_ID,
    IX_API_KEYS_TENANT_ID,
    UQ_API_KEYS_PREFIX,
)
from app.persistence.models.base import Base

_API_KEY_STATUS_VALUES = ("active", "revoked")


class ApiKeyModel(Base):
    """Fila de `api_keys`. Solo guarda el hash del secreto."""

    __tablename__ = "api_keys"
    __table_args__ = (
        UniqueConstraint("prefix", name=UQ_API_KEYS_PREFIX),
        CheckConstraint(
            f"status IN ({', '.join(repr(value) for value in _API_KEY_STATUS_VALUES)})",
            name=CK_API_KEYS_STATUS,
        ),
        Index(IX_API_KEYS_TENANT_ID, "tenant_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", name=FK_API_KEYS_TENANT_ID, ondelete="RESTRICT"),
        nullable=False,
    )
    prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
