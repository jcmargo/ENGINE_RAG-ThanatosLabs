"""Dominio puro de identidad: empresas, usuarios y membresías."""

from app.domain.entities import ApiKey, AuthPrincipal, AuthSession, Membership, Tenant, User
from app.domain.enums import (
    ALLOWED_API_KEY_SCOPES,
    ApiKeyScope,
    ApiKeyStatus,
    AuthSessionStatus,
    PrincipalType,
    SystemRole,
    TenantRole,
    TenantStatus,
    UserStatus,
)
from app.domain.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    DomainError,
    InvalidStateError,
    NotFoundError,
)
from app.domain.normalize import normalize_email, normalize_slug
from app.domain.transitions import transition_tenant_status, transition_user_status

__all__ = [
    "ALLOWED_API_KEY_SCOPES",
    "ApiKey",
    "ApiKeyScope",
    "ApiKeyStatus",
    "AuthPrincipal",
    "AuthSession",
    "AuthSessionStatus",
    "AuthenticationError",
    "AuthorizationError",
    "ConflictError",
    "DomainError",
    "InvalidStateError",
    "Membership",
    "NotFoundError",
    "PrincipalType",
    "SystemRole",
    "Tenant",
    "TenantRole",
    "TenantStatus",
    "User",
    "UserStatus",
    "normalize_email",
    "normalize_slug",
    "transition_tenant_status",
    "transition_user_status",
]
