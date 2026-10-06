"""Datasets, casos y corridas de evaluación.

Revision ID: 0008_evaluation
Revises: 0007_history_scope_retrieval
Create Date: 2026-10-05

El texto del caso queda en evaluation_cases. La corrida no copia el documento.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_evaluation"
down_revision: str | None = "0007_history_scope_retrieval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DATASET_STATUS = "status IN ('draft', 'active', 'archived')"
_CASE_STATUS = "status IN ('active', 'excluded')"
_CASE_SPLIT = "split IN ('calibration', 'test')"
_FIDELITY = (
    "fidelity_label IS NULL OR fidelity_label IN "
    "('supported', 'partially_supported', 'unsupported')"
)
_LABEL_PAIR = (
    "(fidelity_label IS NULL AND labeled_answer_sha256 IS NULL) OR "
    "(fidelity_label IS NOT NULL AND labeled_answer_sha256 IS NOT NULL)"
)
_RUN_STATUS = "status IN ('pending', 'running', 'completed', 'failed')"


def upgrade() -> None:
    """Crea conjuntos, casos y corridas."""
    op.create_table(
        "evaluation_datasets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_DATASET_STATUS, name="ck_evaluation_datasets_status"),
        sa.CheckConstraint("case_count >= 1", name="ck_evaluation_datasets_case_count"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_evaluation_datasets_tenant_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "name",
            "version",
            name="uq_evaluation_datasets_tenant_name_version",
        ),
    )
    op.create_index(
        "ix_evaluation_datasets_tenant_created",
        "evaluation_datasets",
        ["tenant_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "evaluation_cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("case_key", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("split", sa.String(length=32), nullable=False),
        sa.Column("answerable", sa.Boolean(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("expected_sources", postgresql.JSONB(), nullable=False),
        sa.Column("expected_pages", postgresql.JSONB(), nullable=False),
        sa.Column("expected_facts", postgresql.JSONB(), nullable=False),
        sa.Column("expected_patterns", postgresql.JSONB(), nullable=False),
        sa.Column("tags", postgresql.JSONB(), nullable=False),
        sa.Column("fidelity_label", sa.String(length=32), nullable=True),
        sa.Column("label_version", sa.Integer(), nullable=False),
        sa.Column("labeled_answer_sha256", sa.String(length=64), nullable=True),
        sa.CheckConstraint(_CASE_STATUS, name="ck_evaluation_cases_status"),
        sa.CheckConstraint(_CASE_SPLIT, name="ck_evaluation_cases_split"),
        sa.CheckConstraint(_FIDELITY, name="ck_evaluation_cases_fidelity"),
        sa.CheckConstraint(_LABEL_PAIR, name="ck_evaluation_cases_label"),
        sa.CheckConstraint("label_version >= 1", name="ck_evaluation_cases_label_version"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_evaluation_cases_tenant_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["evaluation_datasets.id"],
            name="fk_evaluation_cases_dataset_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "case_key", name="uq_evaluation_cases_dataset_key"),
    )
    op.create_index(
        "ix_evaluation_cases_tenant_dataset",
        "evaluation_cases",
        ["tenant_id", "dataset_id"],
        unique=False,
    )
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("corpus_sha256", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("chunker_version", sa.String(length=64), nullable=False),
        sa.Column("embedding_version", sa.String(length=255), nullable=False),
        sa.Column("index_version", sa.String(length=64), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("min_retrieval_score", sa.Float(), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("llm_version", sa.String(length=255), nullable=False),
        sa.Column("dataset_version", sa.String(length=64), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("failed_cases", postgresql.JSONB(), nullable=False),
        sa.Column("isolation_ok", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_RUN_STATUS, name="ck_evaluation_runs_status"),
        sa.CheckConstraint("top_k >= 1 AND top_k <= 20", name="ck_evaluation_runs_top_k"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_evaluation_runs_tenant_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["evaluation_datasets.id"],
            name="fk_evaluation_runs_dataset_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_evaluation_runs_tenant_dataset",
        "evaluation_runs",
        ["tenant_id", "dataset_id"],
        unique=False,
    )


def downgrade() -> None:
    """Elimina corridas, casos y conjuntos."""
    op.drop_index("ix_evaluation_runs_tenant_dataset", table_name="evaluation_runs")
    op.drop_table("evaluation_runs")
    op.drop_index("ix_evaluation_cases_tenant_dataset", table_name="evaluation_cases")
    op.drop_table("evaluation_cases")
    op.drop_index("ix_evaluation_datasets_tenant_created", table_name="evaluation_datasets")
    op.drop_table("evaluation_datasets")
