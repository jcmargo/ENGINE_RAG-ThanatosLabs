"""Estados y roles de identidad almacenados como texto legible."""

from enum import StrEnum


class TenantStatus(StrEnum):
    """Ciclo de vida de una empresa."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETING = "deleting"


class UserStatus(StrEnum):
    """Ciclo de vida de un usuario global."""

    ACTIVE = "active"
    DISABLED = "disabled"


class SystemRole(StrEnum):
    """Rol global del proveedor. No pertenece a una empresa."""

    PROVIDER_ADMIN = "provider_admin"


class TenantRole(StrEnum):
    """Rol de un usuario dentro de una empresa."""

    TENANT_ADMIN = "tenant_admin"
    MEMBER = "member"


class MembershipStatus(StrEnum):
    """Estado de la membresía dentro de una empresa."""

    ACTIVE = "active"
    DISABLED = "disabled"


class ActivationTokenStatus(StrEnum):
    """Ciclo de un token de activación de un solo uso."""

    PENDING = "pending"
    CONSUMED = "consumed"
    REVOKED = "revoked"


class PrincipalType(StrEnum):
    """Tipo de identidad autenticada."""

    PROVIDER_ADMIN = "provider_admin"
    TENANT_ADMIN = "tenant_admin"
    MEMBER = "member"
    API_KEY = "api_key"


class AuthSessionStatus(StrEnum):
    """Ciclo de vida de una sesión de refresh."""

    ACTIVE = "active"
    ROTATED = "rotated"
    REVOKED = "revoked"


class ApiKeyStatus(StrEnum):
    """Ciclo de vida de una API key."""

    ACTIVE = "active"
    REVOKED = "revoked"


class ApiKeyScope(StrEnum):
    """Scopes versionables permitidos en API keys."""

    QUERY = "query"
    DOCUMENTS_READ = "documents:read"
    DOCUMENTS_WRITE = "documents:write"


ALLOWED_API_KEY_SCOPES: frozenset[ApiKeyScope] = frozenset(ApiKeyScope)
