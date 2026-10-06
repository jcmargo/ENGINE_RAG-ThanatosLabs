"""Fila append-only de auditoría. La retención es el único borrado."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.models.base import Base

_RESULT = "result IN ('success', 'failure')"


class AuditEventModel(Base):
    """Metadatos de una acción crítica. No hay columna de contenido."""

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(_RESULT, name="ck_audit_events_result"),
        Index("ix_audit_events_created", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", name="fk_audit_events_tenant_id", ondelete="RESTRICT"),
        nullable=True,
    )
    actor_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    actor_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    client_ip_prefix: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
