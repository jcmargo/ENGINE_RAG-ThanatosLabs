"""Lease, progreso y versión superseded.

Revision ID: 0005_ingestion_leases
Revises: 0004_documents
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_ingestion_leases"
down_revision: str | None = "0004_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VERSION_STATUS = (
    "status IN ('queued', 'processing', 'ready', 'failed', 'deleting', 'deleted', 'superseded')"
)
_JOB_STATUS = "status IN ('pending', 'leased', 'succeeded', 'failed', 'dead')"
_ACTIVE_SHA = "status NOT IN ('deleting', 'deleted', 'superseded')"
_LEASE = (
    "(status = 'leased' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) "
    "OR (status <> 'leased' AND lease_owner IS NULL AND lease_expires_at IS NULL)"
)


def upgrade() -> None:
    """Añade columnas de lease y progreso sin borrar filas pendientes."""
    op.drop_constraint("ck_document_versions_status", "document_versions", type_="check")
    op.create_check_constraint(
        "ck_document_versions_status",
        "document_versions",
        _VERSION_STATUS,
    )
    op.drop_index("uq_document_versions_tenant_sha256_active", table_name="document_versions")
    op.create_index(
        "uq_document_versions_tenant_sha256_active",
        "document_versions",
        ["tenant_id", "content_sha256"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE_SHA),
    )
    op.add_column(
        "document_versions",
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "document_versions",
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_document_versions_progress",
        "document_versions",
        "progress >= 0 AND progress <= 100",
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE ingestion_jobs SET available_at = created_at WHERE available_at IS NULL")
    op.alter_column("ingestion_jobs", "available_at", nullable=False)
    op.add_column("ingestion_jobs", sa.Column("lease_owner", sa.String(length=64), nullable=True))
    op.add_column(
        "ingestion_jobs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("ingestion_jobs", sa.Column("error_code", sa.String(length=64), nullable=True))
    op.add_column(
        "ingestion_jobs",
        sa.Column("replacement_version_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint("ck_ingestion_jobs_status", "ingestion_jobs", type_="check")
    op.create_check_constraint("ck_ingestion_jobs_status", "ingestion_jobs", _JOB_STATUS)
    op.create_check_constraint(
        "ck_ingestion_jobs_attempts",
        "ingestion_jobs",
        "attempts >= 0 AND max_attempts >= 1 AND attempts <= max_attempts",
    )
    op.create_check_constraint(
        "ck_ingestion_jobs_progress",
        "ingestion_jobs",
        "progress >= 0 AND progress <= 100",
    )
    op.create_check_constraint("ck_ingestion_jobs_lease", "ingestion_jobs", _LEASE)
    op.create_index(
        "ix_ingestion_jobs_pending",
        "ingestion_jobs",
        ["available_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    """Quita el lease y vuelve al estado solo pendiente."""
    op.drop_index("ix_ingestion_jobs_pending", table_name="ingestion_jobs")
    op.drop_constraint("ck_ingestion_jobs_lease", "ingestion_jobs", type_="check")
    op.drop_constraint("ck_ingestion_jobs_progress", "ingestion_jobs", type_="check")
    op.drop_constraint("ck_ingestion_jobs_attempts", "ingestion_jobs", type_="check")
    op.drop_constraint("ck_ingestion_jobs_status", "ingestion_jobs", type_="check")
    op.create_check_constraint(
        "ck_ingestion_jobs_status",
        "ingestion_jobs",
        "status IN ('pending')",
    )
    op.drop_column("ingestion_jobs", "finished_at")
    op.drop_column("ingestion_jobs", "started_at")
    op.drop_column("ingestion_jobs", "replacement_version_id")
    op.drop_column("ingestion_jobs", "error_code")
    op.drop_column("ingestion_jobs", "progress")
    op.drop_column("ingestion_jobs", "lease_expires_at")
    op.drop_column("ingestion_jobs", "lease_owner")
    op.drop_column("ingestion_jobs", "available_at")
    op.drop_column("ingestion_jobs", "max_attempts")
    op.drop_column("ingestion_jobs", "attempts")
    op.drop_constraint("ck_document_versions_progress", "document_versions", type_="check")
    op.drop_column("document_versions", "indexed_at")
    op.drop_column("document_versions", "progress")
    op.execute("UPDATE document_versions SET status = 'deleted' WHERE status = 'superseded'")
    op.drop_index("uq_document_versions_tenant_sha256_active", table_name="document_versions")
    op.create_index(
        "uq_document_versions_tenant_sha256_active",
        "document_versions",
        ["tenant_id", "content_sha256"],
        unique=True,
        postgresql_where=sa.text("status NOT IN ('deleting', 'deleted')"),
    )
    op.drop_constraint("ck_document_versions_status", "document_versions", type_="check")
    op.create_check_constraint(
        "ck_document_versions_status",
        "document_versions",
        "status IN ('queued', 'processing', 'ready', 'failed', 'deleting', 'deleted')",
    )
