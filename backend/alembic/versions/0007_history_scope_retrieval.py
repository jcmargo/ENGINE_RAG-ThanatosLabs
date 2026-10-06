"""Amplía history_scope para admitir retrieval.

Revision ID: 0007_history_scope_retrieval
Revises: 0006_conversations
Create Date: 2026-10-02

Las filas con visual siguen siendo válidas. Esta revisión no las reescribe.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_history_scope_retrieval"
down_revision: str | None = "0006_conversations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCOPE = "history_scope IN ('visual', 'retrieval')"
_VISUAL_ONLY = "history_scope = 'visual'"


def upgrade() -> None:
    """Sustituye el check sin tocar las filas existentes."""
    op.drop_constraint("ck_conversations_history_scope", "conversations", type_="check")
    op.create_check_constraint("ck_conversations_history_scope", "conversations", _SCOPE)


def downgrade() -> None:
    """Vuelve al check de visual. Falla si ya hay filas retrieval."""
    op.drop_constraint("ck_conversations_history_scope", "conversations", type_="check")
    op.create_check_constraint("ck_conversations_history_scope", "conversations", _VISUAL_ONLY)
