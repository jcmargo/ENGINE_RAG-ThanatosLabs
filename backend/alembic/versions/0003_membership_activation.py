"""Estado de membresía y tokens de activación de un solo uso.

Revision ID: 0003_membership_activation
Revises: 0002_auth_sessions_api_keys
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_membership_activation"
down_revision: str | None = "0002_auth_sessions_api_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Añade el estado de membresía y la tabla de activaciones."""
    op.add_column(
        "memberships",
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
    )
    op.create_check_constraint(
        "ck_memberships_status",
        "memberships",
        "status IN ('active', 'disabled')",
    )
    op.create_table(
        "activation_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'consumed', 'revoked')",
            name="ck_activation_tokens_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_activation_tokens_tenant_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_activation_tokens_user_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_activation_tokens_token_hash"),
    )
    op.create_index(
        "ix_activation_tokens_tenant_user",
        "activation_tokens",
        ["tenant_id", "user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Elimina activaciones y el estado de membresía."""
    op.drop_index("ix_activation_tokens_tenant_user", table_name="activation_tokens")
    op.drop_table("activation_tokens")
    op.drop_constraint("ck_memberships_status", "memberships", type_="check")
    op.drop_column("memberships", "status")
