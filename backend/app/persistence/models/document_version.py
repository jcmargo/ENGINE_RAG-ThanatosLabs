"""Modelo ORM de una versión de contenido."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.constraints import (
    CK_DOCUMENT_VERSIONS_PROGRESS,
    CK_DOCUMENT_VERSIONS_STATUS,
    FK_DOCUMENT_VERSIONS_DOCUMENT_ID,
    FK_DOCUMENT_VERSIONS_TENANT_ID,
    IX_DOCUMENT_VERSIONS_DOCUMENT_ID,
    UQ_DOCUMENT_VERSIONS_ACTIVE_SHA256,
    UQ_DOCUMENT_VERSIONS_NUMBER,
)
from app.persistence.models.base import Base

_VERSION_STATUS_VALUES = (
    "queued",
    "processing",
    "ready",
    "failed",
    "deleting",
    "deleted",
    "superseded",
)


class DocumentVersionModel(Base):
    """Fila de `document_versions`."""

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name=UQ_DOCUMENT_VERSIONS_NUMBER),
        CheckConstraint(
            f"status IN ({', '.join(repr(value) for value in _VERSION_STATUS_VALUES)})",
            name=CK_DOCUMENT_VERSIONS_STATUS,
        ),
        CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name=CK_DOCUMENT_VERSIONS_PROGRESS,
        ),
        Index(
            UQ_DOCUMENT_VERSIONS_ACTIVE_SHA256,
            "tenant_id",
            "content_sha256",
            unique=True,
            postgresql_where=text("status NOT IN ('deleting', 'deleted', 'superseded')"),
        ),
        Index(IX_DOCUMENT_VERSIONS_DOCUMENT_ID, "document_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", name=FK_DOCUMENT_VERSIONS_TENANT_ID, ondelete="RESTRICT"),
        nullable=False,
    )
    document_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", name=FK_DOCUMENT_VERSIONS_DOCUMENT_ID, ondelete="RESTRICT"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    progress: Mapped[int] = mapped_column(Integer, nullable=False)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    public_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
