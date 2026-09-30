"""Modelo ORM de empresas."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.constraints import CK_TENANTS_STATUS, UQ_TENANTS_SLUG
from app.persistence.models.base import Base

_TENANT_STATUS_VALUES = ("active", "suspended", "deleting")


class TenantModel(Base):
    """Fila de `tenants`."""

    __tablename__ = "tenants"
    __table_args__ = (
        UniqueConstraint("slug", name=UQ_TENANTS_SLUG),
        CheckConstraint(
            f"status IN ({', '.join(repr(value) for value in _TENANT_STATUS_VALUES)})",
            name=CK_TENANTS_STATUS,
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
