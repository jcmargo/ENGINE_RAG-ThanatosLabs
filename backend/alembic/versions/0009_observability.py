"""Auditoría, rate limit y correlación de la carga con el worker.

Revision ID: 0009_observability
Revises: 0008_evaluation
Create Date: 2026-10-05

La auditoría no guarda contenido. El bucket guarda un HMAC, no un id.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_observability"
down_revision: str | None = "0008_evaluation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Añade auditoría, buckets y el id de la solicitud que creó el trabajo."""
    op.add_column(
        "ingestion_jobs",
        sa.Column("origin_request_id", sa.Uuid(), nullable=True),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("actor_kind", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("client_ip_prefix", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("result IN ('success', 'failure')", name="ck_audit_events_result"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_audit_events_tenant_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_created", "audit_events", ["created_at"], unique=False)
    op.create_table(
        "rate_limit_buckets",
        sa.Column("bucket_key", sa.String(length=64), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("bucket_key", "window_start", name="pk_rate_limit_buckets"),
    )


def downgrade() -> None:
    """Quita buckets, auditoría y la columna de correlación."""
    op.drop_table("rate_limit_buckets")
    op.drop_index("ix_audit_events_created", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_column("ingestion_jobs", "origin_request_id")
