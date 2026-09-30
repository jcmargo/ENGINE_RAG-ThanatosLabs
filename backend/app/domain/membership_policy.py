"""Reglas puras de administración de membresías."""

from app.domain.enums import MembershipStatus, TenantRole
from app.domain.errors import ConflictError


def assert_last_tenant_admin_remains(
    *,
    target_role: TenantRole,
    target_status: MembershipStatus,
    active_admin_count: int,
    next_role: TenantRole | None = None,
    next_status: MembershipStatus | None = None,
) -> None:
    """Impide degradar o desactivar al último administrador activo."""
    if target_role is not TenantRole.TENANT_ADMIN:
        return
    if target_status is not MembershipStatus.ACTIVE:
        return
    loses_admin = False
    if next_role is not None and next_role is not TenantRole.TENANT_ADMIN:
        loses_admin = True
    if next_status is not None and next_status is not MembershipStatus.ACTIVE:
        loses_admin = True
    if loses_admin and active_admin_count <= 1:
        message = "No se puede degradar ni desactivar al último administrador activo"
        raise ConflictError(message)
