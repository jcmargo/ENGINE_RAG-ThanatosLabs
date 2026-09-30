"""Modelo ORM de membresías usuario-empresa."""

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
    CK_MEMBERSHIPS_ROLE,
    CK_MEMBERSHIPS_STATUS,
    FK_MEMBERSHIPS_TENANT_ID,
    FK_MEMBERSHIPS_USER_ID,
    IX_MEMBERSHIPS_USER_ID,
    UQ_MEMBERSHIPS_TENANT_USER,
)
from app.persistence.models.base import Base

_TENANT_ROLE_VALUES = ("tenant_admin", "member")
_MEMBERSHIP_STATUS_VALUES = ("active", "disabled")


class MembershipModel(Base):
    """Fila de `memberships`."""

    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name=UQ_MEMBERSHIPS_TENANT_USER),
        CheckConstraint(
            f"role IN ({', '.join(repr(value) for value in _TENANT_ROLE_VALUES)})",
            name=CK_MEMBERSHIPS_ROLE,
        ),
        CheckConstraint(
            f"status IN ({', '.join(repr(value) for value in _MEMBERSHIP_STATUS_VALUES)})",
            name=CK_MEMBERSHIPS_STATUS,
        ),
        Index(IX_MEMBERSHIPS_USER_ID, "user_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", name=FK_MEMBERSHIPS_TENANT_ID, ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name=FK_MEMBERSHIPS_USER_ID, ondelete="RESTRICT"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
