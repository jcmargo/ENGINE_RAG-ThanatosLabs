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
    DocumentVersionStatus,
    IngestionJobStatus,
    IngestionJobType,
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


def _require_progress(progress: int) -> None:
    if isinstance(progress, bool) or not isinstance(progress, int) or not 0 <= progress <= 100:
        message = "El progreso debe ser un entero entre 0 y 100"
        raise InvalidStateError(message)


def _require_attempt_counts(attempts: int, max_attempts: int) -> None:
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
        message = "Los intentos del trabajo no son válidos"
        raise InvalidStateError(message)
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
        message = "El máximo de intentos del trabajo no es válido"
        raise InvalidStateError(message)
    if attempts > max_attempts:
        message = "Los intentos superan el máximo del trabajo"
        raise InvalidStateError(message)


def _require_error_code(error_code: str | None) -> None:
    if error_code is None:
        return
    if not error_code or len(error_code) > 64 or any(character == " " for character in error_code):
        message = "El código de error del trabajo no es válido"
        raise InvalidStateError(message)


def _require_lease(
    status: IngestionJobStatus,
    lease_owner: str | None,
    lease_expires_at: datetime | None,
) -> None:
    if status is IngestionJobStatus.LEASED:
        if lease_owner is None or not lease_owner.strip() or lease_expires_at is None:
            message = "Un trabajo alquilado necesita dueño y vencimiento"
            raise InvalidStateError(message)
        _require_utc(lease_expires_at, "lease_expires_at")
        return
    if lease_owner is not None or lease_expires_at is not None:
        message = "Solo un trabajo alquilado conserva el lease"
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
            PrincipalType.WORKER,
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
    api_key_id: UUID | None = None


@dataclass(frozen=True)
class Document:
    """Documento lógico de una empresa. El contenido vive en sus versiones."""

    id: UUID
    tenant_id: UUID
    display_name: str
    status: DocumentVersionStatus
    current_version_id: UUID
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.display_name.strip() or "/" in self.display_name or "\\" in self.display_name:
            message = "El nombre visible del documento no es válido"
            raise InvalidStateError(message)
        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")


@dataclass(frozen=True)
class DocumentVersion:
    """Una revisión de contenido, separada del documento lógico."""

    id: UUID
    tenant_id: UUID
    document_id: UUID
    version_number: int
    status: DocumentVersionStatus
    mime_type: str
    byte_size: int
    content_sha256: str
    storage_key: str
    pipeline_version: str
    chunk_count: int
    created_at: datetime
    updated_at: datetime
    public_error_code: str | None = None
    progress: int = 0
    indexed_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.version_number < 1:
            message = "El número de versión debe ser positivo"
            raise InvalidStateError(message)
        if not self.mime_type or not self.pipeline_version:
            message = "La versión de documento necesita MIME y pipeline"
            raise InvalidStateError(message)
        if self.byte_size < 1 or self.chunk_count < 0:
            message = "El tamaño o el conteo de fragmentos no es válido"
            raise InvalidStateError(message)
        if len(self.content_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.content_sha256
        ):
            message = "El SHA-256 del contenido no es válido"
            raise InvalidStateError(message)
        if not self.storage_key or ".." in self.storage_key.split("/"):
            message = "La ubicación interna del documento no es válida"
            raise InvalidStateError(message)
        _require_progress(self.progress)
        if self.indexed_at is not None:
            _require_utc(self.indexed_at, "indexed_at")
        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")


@dataclass(frozen=True)
class IngestionJob:
    """Trabajo pendiente de ingesta, reindexación o eliminación."""

    id: UUID
    tenant_id: UUID
    document_id: UUID
    document_version_id: UUID
    job_type: IngestionJobType
    status: IngestionJobStatus
    created_at: datetime
    updated_at: datetime
    attempts: int = 0
    max_attempts: int = 5
    available_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    progress: int = 0
    error_code: str | None = None
    replacement_version_id: UUID | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    origin_request_id: UUID | None = None

    def __post_init__(self) -> None:
        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")
        if self.available_at is None:
            object.__setattr__(self, "available_at", self.created_at)
        else:
            _require_utc(self.available_at, "available_at")
        _require_attempt_counts(self.attempts, self.max_attempts)
        _require_progress(self.progress)
        _require_error_code(self.error_code)
        _require_lease(self.status, self.lease_owner, self.lease_expires_at)
        if self.started_at is not None:
            _require_utc(self.started_at, "started_at")
        if self.finished_at is not None:
            _require_utc(self.finished_at, "finished_at")


@dataclass(frozen=True)
class DocumentView:
    """Ficha visible. No incluye ruta interna, hash ni contenido."""

    id: UUID
    display_name: str
    status: DocumentVersionStatus
    version_number: int
    byte_size: int
    mime_type: str
    created_at: datetime
    updated_at: datetime
    chunk_count: int
    version_id: UUID
    progress: int = 0
    public_error_code: str | None = None

    def __post_init__(self) -> None:
        _require_progress(self.progress)
        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")


@dataclass(frozen=True)
class RetrievableVersion:
    """Versión lista y actual que se puede citar. No incluye el texto del fragmento."""

    document_id: UUID
    version_id: UUID
    display_name: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.display_name, str)
            or not self.display_name.strip()
            or "/" in self.display_name
            or "\\" in self.display_name
        ):
            message = "El nombre visible del documento no es válido"
            raise InvalidStateError(message)
