"""Entidades inmutables de identidad, separadas del ORM."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from app.domain.enums import (
    ActivationTokenStatus,
    ApiKeyScope,
    ApiKeyStatus,
    AuthSessionStatus,
    MembershipStatus,
    PrincipalType,
    SystemRole,
    TenantRole,
    TenantStatus,
    UserStatus,
)
from app.domain.errors import InvalidStateError
from app.domain.normalize import normalize_email, normalize_slug


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        message = f"{field_name} debe ser timezone-aware en UTC"
        raise InvalidStateError(message)
    if value.utcoffset() != timedelta(0):
        message = f"{field_name} debe tener offset UTC (0)"
        raise InvalidStateError(message)


@dataclass(frozen=True)
class Tenant:
    """Empresa con slug normalizado y estado explícito."""

    id: UUID
    name: str
    slug: str
    status: TenantStatus
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.name.strip():
            message = "El nombre de la empresa no puede estar vacío"
            raise InvalidStateError(message)
        if self.slug != normalize_slug(self.slug):
            message = "El slug de la empresa debe estar normalizado"
            raise InvalidStateError(message)
        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")


@dataclass(frozen=True)
class User:
    """Identidad global. `system_role` es opcional; `password_hash` es opaco."""

    id: UUID
    email: str
    password_hash: str
    status: UserStatus
    created_at: datetime
    updated_at: datetime
    system_role: SystemRole | None = None

    def __post_init__(self) -> None:
        if self.email != normalize_email(self.email):
            message = "El email del usuario debe estar normalizado"
            raise InvalidStateError(message)
        if not self.password_hash:
            message = "El hash de contraseña no puede estar vacío"
            raise InvalidStateError(message)
        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")


@dataclass(frozen=True)
class Membership:
    """Asociación usuario-empresa con rol obligatorio."""

    id: UUID
    tenant_id: UUID
    user_id: UUID
    role: TenantRole
    created_at: datetime
    updated_at: datetime
    status: MembershipStatus = MembershipStatus.ACTIVE

    def __post_init__(self) -> None:
        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")


@dataclass(frozen=True)
class AuthSession:
    """Sesión de refresh: solo hash del token, sin secreto en claro."""

    id: UUID
    user_id: UUID
    refresh_token_hash: str
    status: AuthSessionStatus
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    tenant_id: UUID | None = None
    replaced_by_session_id: UUID | None = None

    def __post_init__(self) -> None:
        if not self.refresh_token_hash:
            message = "El hash del refresh no puede estar vacío"
            raise InvalidStateError(message)
        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")
        _require_utc(self.expires_at, "expires_at")


@dataclass(frozen=True)
class ApiKey:
    """Credencial de integración: prefijo identificable y hash del secreto."""

    id: UUID
    tenant_id: UUID
    prefix: str
    secret_hash: str
    status: ApiKeyStatus
    scopes: tuple[ApiKeyScope, ...]
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.prefix.startswith("rag_"):
            message = "El prefijo de API key debe empezar por rag_"
            raise InvalidStateError(message)
        if not self.secret_hash:
            message = "El hash del secreto de API key no puede estar vacío"
            raise InvalidStateError(message)
        if not self.scopes:
            message = "Una API key debe declarar al menos un scope"
            raise InvalidStateError(message)
        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")
        if self.revoked_at is not None:
            _require_utc(self.revoked_at, "revoked_at")


@dataclass(frozen=True)
class ActivationToken:
    """Invitación de un solo uso. Solo se persiste el hash del token."""

    id: UUID
    tenant_id: UUID
    user_id: UUID
    token_hash: str
    status: ActivationTokenStatus
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    consumed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.token_hash:
            message = "El hash del token de activación no puede estar vacío"
            raise InvalidStateError(message)
        _require_utc(self.expires_at, "expires_at")
        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")
        if self.consumed_at is not None:
            _require_utc(self.consumed_at, "consumed_at")


@dataclass(frozen=True)
class TenantMember:
    """Usuario visto desde una empresa. No transporta el tenant del cliente."""

    user_id: UUID
    email: str
    role: TenantRole
    status: MembershipStatus
    created_at: datetime
    membership_id: UUID

    def __post_init__(self) -> None:
        if self.email != normalize_email(self.email):
            message = "El email del miembro debe estar normalizado"
            raise InvalidStateError(message)
        _require_utc(self.created_at, "created_at")


@dataclass(frozen=True)
class TenantContext:
    """Alcance de empresa construido por autenticación, no por el cliente."""

    tenant_id: UUID
    principal: AuthPrincipal

    def __post_init__(self) -> None:
        if self.principal.tenant_id != self.tenant_id:
            message = "El principal no pertenece a la empresa del contexto"
            raise InvalidStateError(message)
        if self.principal.type not in {
            PrincipalType.TENANT_ADMIN,
            PrincipalType.MEMBER,
            PrincipalType.API_KEY,
        }:
            message = "El contexto de empresa no admite un principal global"
            raise InvalidStateError(message)


@dataclass(frozen=True)
class AuthPrincipal:
    """Identidad autenticada validada contra PostgreSQL."""

    type: PrincipalType
    user_id: UUID | None
    tenant_id: UUID | None
    role: TenantRole | None
    scopes: tuple[ApiKeyScope, ...]
