"""Alcance de empresa para un trabajo ya persistido por la API."""

from uuid import UUID

from app.domain.entities import AuthPrincipal, TenantContext
from app.domain.enums import PrincipalType


def context_for_job(tenant_id: UUID) -> TenantContext:
    """Construye el contexto desde la fila del trabajo, no desde un cuerpo HTTP."""
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
