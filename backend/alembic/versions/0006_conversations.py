"""Conversaciones, feedback, trazas, uso e idempotencia.

Revision ID: 0006_conversations
Revises: 0005_ingestion_leases
Create Date: 2026-10-01

El texto de la pregunta vive en messages. Trazas y uso no lo copian.
El vencimiento es lógico: no hay borrado físico.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_conversations"
down_revision: str | None = "0005_ingestion_leases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OWNER_KIND = "owner_kind IN ('user', 'api_key')"
_AUTHOR_KIND = "author_kind IN ('user', 'api_key')"


def upgrade() -> None:
    """Crea el historial visual, la traza técnica y los agregados de uso."""
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("owner_kind", sa.String(length=16), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("history_scope", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_OWNER_KIND, name="ck_conversations_owner_kind"),
        sa.CheckConstraint("history_scope = 'visual'", name="ck_conversations_history_scope"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_conversations_tenant_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversations_owner",
        "conversations",
        ["tenant_id", "owner_kind", "owner_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", postgresql.JSONB(), nullable=False),
        sa.Column("abstained", sa.Boolean(), nullable=True),
        sa.Column("pipeline_version", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_messages_tenant_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_messages_conversation_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_messages_conversation",
        "messages",
        ["conversation_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_messages_tenant_request",
        "messages",
        ["tenant_id", "request_id"],
        unique=False,
    )
    op.create_table(
        "query_traces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("embedding_version", sa.String(length=255), nullable=False),
        sa.Column("index_version", sa.String(length=64), nullable=False),
        sa.Column("pipeline_version", sa.String(length=255), nullable=False),
        sa.Column("hits_retrieved", sa.Integer(), nullable=False),
        sa.Column("hits_after_validation", sa.Integer(), nullable=False),
        sa.Column("hits_after_threshold", sa.Integer(), nullable=False),
        sa.Column("returned", sa.Integer(), nullable=False),
        sa.Column("rounds", sa.Integer(), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("min_retrieval_score", sa.Float(), nullable=False),
        sa.Column("embed_ms", sa.Float(), nullable=False),
        sa.Column("search_ms", sa.Float(), nullable=False),
        sa.Column("validate_ms", sa.Float(), nullable=False),
        sa.Column("generation_ms", sa.Float(), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=False),
        sa.Column("result_code", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "result_code IN ('answered', 'abstained', 'error')",
            name="ck_query_traces_result",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_query_traces_tenant_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name="fk_query_traces_message_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "request_id", name="uq_query_traces_tenant_request"),
    )
    op.create_index("ix_query_traces_expires", "query_traces", ["expires_at"], unique=False)
    op.create_table(
        "feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("author_kind", sa.String(length=16), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("rating IN ('helpful', 'not_helpful')", name="ck_feedback_rating"),
        sa.CheckConstraint(_AUTHOR_KIND, name="ck_feedback_author_kind"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_feedback_tenant_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name="fk_feedback_message_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "message_id",
            "author_kind",
            "author_id",
            name="uq_feedback_message_author",
        ),
    )
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("token_estimate", sa.Integer(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('upload', 'chunks', 'question')",
            name="ck_usage_events_kind",
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('answered', 'abstained', 'error')",
            name="ck_usage_events_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_usage_events_tenant_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_id",
            "kind",
            name="uq_usage_events_tenant_source_kind",
        ),
    )
    op.create_index(
        "ix_usage_events_tenant_occurred",
        "usage_events",
        ["tenant_id", "occurred_at"],
        unique=False,
    )
    op.create_table(
        "question_idempotency",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("owner_kind", sa.String(length=16), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_OWNER_KIND, name="ck_question_idempotency_owner_kind"),
        sa.CheckConstraint(
            "status IN ('pending', 'completed')",
            name="ck_question_idempotency_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_question_idempotency_tenant_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "owner_kind",
            "owner_id",
            "key_hash",
            name="uq_question_idempotency_owner_key",
        ),
    )


def downgrade() -> None:
    """Quita el historial. No toca documentos ni identidad."""
    op.drop_table("question_idempotency")
    op.drop_index("ix_usage_events_tenant_occurred", table_name="usage_events")
    op.drop_table("usage_events")
    op.drop_table("feedback")
    op.drop_index("ix_query_traces_expires", table_name="query_traces")
    op.drop_table("query_traces")
    op.drop_index("ix_messages_tenant_request", table_name="messages")
    op.drop_index("ix_messages_conversation", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conversations_owner", table_name="conversations")
    op.drop_table("conversations")
