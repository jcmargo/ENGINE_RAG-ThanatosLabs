"""Backups, tombstones y paquetes de exportación.

Revision ID: 0010_lifecycle
Revises: 0009_observability
Create Date: 2026-10-05

El tombstone guarda un HMAC, no el nombre ni el email de la empresa.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_lifecycle"
down_revision: str | None = "0009_observability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Añade la corrida de backup, el tombstone y el paquete cifrado."""
    op.create_table(
        "backup_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_backup_runs_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backup_runs_active", "backup_runs", ["status", "expires_at"], unique=False)
    op.create_table(
        "tenant_tombstones",
        sa.Column("tenant_hmac", sa.String(length=64), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_hmac"),
    )
    op.create_table(
        "export_packages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("nonce_hex", sa.String(length=24), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_export_packages_tenant_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_export_packages_token_hash"),
    )
    op.create_index(
        "ix_export_packages_expires",
        "export_packages",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """Quita exportación, tombstones y corridas de backup."""
    op.drop_index("ix_export_packages_expires", table_name="export_packages")
    op.drop_table("export_packages")
    op.drop_table("tenant_tombstones")
    op.drop_index("ix_backup_runs_active", table_name="backup_runs")
    op.drop_table("backup_runs")
