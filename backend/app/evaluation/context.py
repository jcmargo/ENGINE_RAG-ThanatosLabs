"""Alcance de empresa para una corrida de evaluación ya ligada a un tenant."""

from uuid import UUID

from app.domain.entities import AuthPrincipal, TenantContext
from app.domain.enums import PrincipalType


def context_for_evaluation(tenant_id: UUID) -> TenantContext:
    """Construye el contexto desde la empresa persistida, no desde el JSONL."""
    return TenantContext(
        tenant_id=tenant_id,
        principal=AuthPrincipal(
            type=PrincipalType.WORKER,
            user_id=None,
            tenant_id=tenant_id,
            role=None,
            scopes=(),
        ),
    )
