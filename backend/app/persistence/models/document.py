"""Modelo ORM del documento lógico."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.constraints import (
    CK_DOCUMENTS_STATUS,
    FK_DOCUMENTS_TENANT_ID,
    IX_DOCUMENTS_TENANT_CREATED,
    UQ_DOCUMENTS_CURRENT_VERSION,
)
from app.persistence.models.base import Base

_DOCUMENT_STATUS_VALUES = (
    "queued",
    "processing",
    "ready",
    "failed",
    "deleting",
    "deleted",
)


class DocumentModel(Base):
    """Fila de `documents`. La versión actual es un UUID, sin FK circular."""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("current_version_id", name=UQ_DOCUMENTS_CURRENT_VERSION),
        CheckConstraint(
            f"status IN ({', '.join(repr(value) for value in _DOCUMENT_STATUS_VALUES)})",
            name=CK_DOCUMENTS_STATUS,
        ),
        Index(IX_DOCUMENTS_TENANT_CREATED, "tenant_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", name=FK_DOCUMENTS_TENANT_ID, ondelete="RESTRICT"),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
