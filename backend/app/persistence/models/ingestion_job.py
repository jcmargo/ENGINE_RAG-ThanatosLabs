"""Modelo ORM de un trabajo de ingesta con lease."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.constraints import (
    CK_INGESTION_JOBS_ATTEMPTS,
    CK_INGESTION_JOBS_LEASE,
    CK_INGESTION_JOBS_PROGRESS,
    CK_INGESTION_JOBS_STATUS,
    CK_INGESTION_JOBS_TYPE,
    FK_INGESTION_JOBS_DOCUMENT_ID,
    FK_INGESTION_JOBS_TENANT_ID,
    FK_INGESTION_JOBS_VERSION_ID,
    IX_INGESTION_JOBS_DOCUMENT_ID,
    IX_INGESTION_JOBS_PENDING,
)
from app.persistence.models.base import Base

_JOB_TYPES = ("ingest", "reindex", "delete")
_JOB_STATUSES = ("pending", "leased", "succeeded", "failed", "dead")


class IngestionJobModel(Base):
    """Fila de `ingestion_jobs`. El worker reclama con `FOR UPDATE SKIP LOCKED`."""

    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        CheckConstraint(
            f"job_type IN ({', '.join(repr(value) for value in _JOB_TYPES)})",
            name=CK_INGESTION_JOBS_TYPE,
        ),
        CheckConstraint(
            f"status IN ({', '.join(repr(value) for value in _JOB_STATUSES)})",
            name=CK_INGESTION_JOBS_STATUS,
        ),
        CheckConstraint(
            "attempts >= 0 AND max_attempts >= 1 AND attempts <= max_attempts",
            name=CK_INGESTION_JOBS_ATTEMPTS,
        ),
        CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name=CK_INGESTION_JOBS_PROGRESS,
        ),
        CheckConstraint(
            "("
            "status = 'leased' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL"
            ") OR ("
            "status <> 'leased' AND lease_owner IS NULL AND lease_expires_at IS NULL"
            ")",
            name=CK_INGESTION_JOBS_LEASE,
        ),
        Index(IX_INGESTION_JOBS_DOCUMENT_ID, "document_id"),
        Index(
            IX_INGESTION_JOBS_PENDING,
            "available_at",
            "id",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", name=FK_INGESTION_JOBS_TENANT_ID, ondelete="RESTRICT"),
        nullable=False,
    )
    document_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", name=FK_INGESTION_JOBS_DOCUMENT_ID, ondelete="RESTRICT"),
        nullable=False,
    )
    document_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "document_versions.id",
            name=FK_INGESTION_JOBS_VERSION_ID,
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    progress: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    replacement_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    origin_request_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
