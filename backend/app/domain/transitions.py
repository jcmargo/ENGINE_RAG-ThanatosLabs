"""Transiciones puras de estado para empresas y usuarios."""

from app.domain.enums import MembershipStatus, TenantStatus, UserStatus
from app.domain.errors import InvalidStateError

_TENANT_TRANSITIONS: frozenset[tuple[TenantStatus, TenantStatus]] = frozenset(
    {
        (TenantStatus.ACTIVE, TenantStatus.SUSPENDED),
        (TenantStatus.SUSPENDED, TenantStatus.ACTIVE),
        (TenantStatus.ACTIVE, TenantStatus.DELETING),
        (TenantStatus.SUSPENDED, TenantStatus.DELETING),
    }
)

_USER_TRANSITIONS: frozenset[tuple[UserStatus, UserStatus]] = frozenset(
    {
        (UserStatus.ACTIVE, UserStatus.DISABLED),
        (UserStatus.DISABLED, UserStatus.ACTIVE),
    }
)


def transition_tenant_status(current: TenantStatus, target: TenantStatus) -> TenantStatus:
    """Aplica una transición válida de empresa o lanza `InvalidStateError`."""
    if (current, target) not in _TENANT_TRANSITIONS:
        message = f"Transición de empresa inválida: {current} -> {target}"
        raise InvalidStateError(message)
    return target


def transition_membership_status(
    current: MembershipStatus,
    target: MembershipStatus,
) -> MembershipStatus:
    """Activa o desactiva una membresía. El mismo estado no es una transición."""
    allowed = {
        (MembershipStatus.ACTIVE, MembershipStatus.DISABLED),
        (MembershipStatus.DISABLED, MembershipStatus.ACTIVE),
    }
    if (current, target) not in allowed:
        message = f"Transición de membresía inválida: {current} -> {target}"
        raise InvalidStateError(message)
    return target


def transition_user_status(current: UserStatus, target: UserStatus) -> UserStatus:
    """Aplica una transición válida de usuario o lanza `InvalidStateError`."""
    if (current, target) not in _USER_TRANSITIONS:
        message = f"Transición de usuario inválida: {current} -> {target}"
        raise InvalidStateError(message)
    return target
