"""Conversión explícita entre modelos ORM y entidades de dominio."""

from app.domain.entities import (
    ActivationToken,
    ApiKey,
    AuthSession,
    Document,
    DocumentVersion,
    IngestionJob,
    Membership,
    Tenant,
    TenantMember,
    User,
)
from app.domain.enums import (
    ActivationTokenStatus,
    ApiKeyScope,
    ApiKeyStatus,
    AuthSessionStatus,
    DocumentVersionStatus,
    IngestionJobStatus,
    IngestionJobType,
    MembershipStatus,
    SystemRole,
    TenantRole,
    TenantStatus,
    UserStatus,
)
from app.domain.errors import InvalidStateError
from app.persistence.models import (
    ActivationTokenModel,
    ApiKeyModel,
    AuthSessionModel,
    DocumentModel,
    DocumentVersionModel,
    IngestionJobModel,
    MembershipModel,
    TenantModel,
    UserModel,
)


def tenant_to_domain(model: TenantModel) -> Tenant:
    """Mapea una fila de `tenants` a entidad de dominio."""
    return Tenant(
        id=model.id,
        name=model.name,
        slug=model.slug,
        status=TenantStatus(model.status),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def tenant_to_model(entity: Tenant) -> TenantModel:
    """Mapea una entidad `Tenant` a fila ORM."""
    return TenantModel(
        id=entity.id,
        name=entity.name,
        slug=entity.slug,
        status=entity.status.value,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )


def user_to_domain(model: UserModel) -> User:
    """Mapea una fila de `users` a entidad de dominio."""
    system_role = SystemRole(model.system_role) if model.system_role is not None else None
    return User(
        id=model.id,
        email=model.email,
        password_hash=model.password_hash,
        status=UserStatus(model.status),
        system_role=system_role,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def user_to_model(entity: User) -> UserModel:
    """Mapea una entidad `User` a fila ORM."""
    return UserModel(
        id=entity.id,
        email=entity.email,
        password_hash=entity.password_hash,
        status=entity.status.value,
        system_role=entity.system_role.value if entity.system_role is not None else None,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )


def membership_to_domain(model: MembershipModel) -> Membership:
    """Mapea una fila de `memberships` a entidad de dominio."""
    return Membership(
        id=model.id,
        tenant_id=model.tenant_id,
        user_id=model.user_id,
        role=TenantRole(model.role),
        status=MembershipStatus(model.status),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def membership_to_model(entity: Membership) -> MembershipModel:
    """Mapea una entidad `Membership` a fila ORM."""
    return MembershipModel(
        id=entity.id,
        tenant_id=entity.tenant_id,
        user_id=entity.user_id,
        role=entity.role.value,
        status=entity.status.value,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )


def auth_session_to_domain(model: AuthSessionModel) -> AuthSession:
    """Mapea una fila de `auth_sessions` a entidad de dominio."""
    return AuthSession(
        id=model.id,
        user_id=model.user_id,
        tenant_id=model.tenant_id,
        refresh_token_hash=model.refresh_token_hash,
        status=AuthSessionStatus(model.status),
        created_at=model.created_at,
        updated_at=model.updated_at,
        expires_at=model.expires_at,
        replaced_by_session_id=model.replaced_by_session_id,
    )


def auth_session_to_model(entity: AuthSession) -> AuthSessionModel:
    """Mapea una entidad `AuthSession` a fila ORM."""
    return AuthSessionModel(
        id=entity.id,
        user_id=entity.user_id,
        tenant_id=entity.tenant_id,
        refresh_token_hash=entity.refresh_token_hash,
        status=entity.status.value,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
        expires_at=entity.expires_at,
        replaced_by_session_id=entity.replaced_by_session_id,
    )


def _parse_scopes(raw: list[str]) -> tuple[ApiKeyScope, ...]:
    scopes: list[ApiKeyScope] = []
    for value in raw:
        try:
            scopes.append(ApiKeyScope(value))
        except ValueError as error:
            message = f"Scope de API key desconocido en persistencia: {value}"
            raise InvalidStateError(message) from error
    return tuple(scopes)


def api_key_to_domain(model: ApiKeyModel) -> ApiKey:
    """Mapea una fila de `api_keys` a entidad de dominio."""
    return ApiKey(
        id=model.id,
        tenant_id=model.tenant_id,
        prefix=model.prefix,
        secret_hash=model.secret_hash,
        status=ApiKeyStatus(model.status),
        scopes=_parse_scopes(list(model.scopes)),
        created_at=model.created_at,
        updated_at=model.updated_at,
        revoked_at=model.revoked_at,
    )


def api_key_to_model(entity: ApiKey) -> ApiKeyModel:
    """Mapea una entidad `ApiKey` a fila ORM."""
    return ApiKeyModel(
        id=entity.id,
        tenant_id=entity.tenant_id,
        prefix=entity.prefix,
        secret_hash=entity.secret_hash,
        status=entity.status.value,
        scopes=[scope.value for scope in entity.scopes],
        created_at=entity.created_at,
        updated_at=entity.updated_at,
        revoked_at=entity.revoked_at,
    )


def activation_token_to_domain(model: ActivationTokenModel) -> ActivationToken:
    """Mapea una fila de `activation_tokens` a entidad de dominio."""
    return ActivationToken(
        id=model.id,
        tenant_id=model.tenant_id,
        user_id=model.user_id,
        token_hash=model.token_hash,
        status=ActivationTokenStatus(model.status),
        expires_at=model.expires_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
        consumed_at=model.consumed_at,
    )


def activation_token_to_model(entity: ActivationToken) -> ActivationTokenModel:
    """Mapea una entidad `ActivationToken` a fila ORM."""
    return ActivationTokenModel(
        id=entity.id,
        tenant_id=entity.tenant_id,
        user_id=entity.user_id,
        token_hash=entity.token_hash,
        status=entity.status.value,
        expires_at=entity.expires_at,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
        consumed_at=entity.consumed_at,
    )


def document_to_domain(model: DocumentModel) -> Document:
    """Mapea una fila de `documents` a entidad de dominio."""
    return Document(
        id=model.id,
        tenant_id=model.tenant_id,
        display_name=model.display_name,
        status=DocumentVersionStatus(model.status),
        current_version_id=model.current_version_id,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def document_to_model(entity: Document) -> DocumentModel:
    """Mapea una entidad `Document` a fila ORM."""
    return DocumentModel(
        id=entity.id,
        tenant_id=entity.tenant_id,
        display_name=entity.display_name,
        status=entity.status.value,
        current_version_id=entity.current_version_id,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )


def document_version_to_domain(model: DocumentVersionModel) -> DocumentVersion:
    """Mapea una fila de `document_versions` a entidad de dominio."""
    return DocumentVersion(
        id=model.id,
        tenant_id=model.tenant_id,
        document_id=model.document_id,
        version_number=model.version_number,
        status=DocumentVersionStatus(model.status),
        mime_type=model.mime_type,
        byte_size=int(model.byte_size),
        content_sha256=model.content_sha256,
        storage_key=model.storage_key,
        pipeline_version=model.pipeline_version,
        chunk_count=model.chunk_count,
        public_error_code=model.public_error_code,
        progress=model.progress,
        indexed_at=model.indexed_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def document_version_to_model(entity: DocumentVersion) -> DocumentVersionModel:
    """Mapea una entidad `DocumentVersion` a fila ORM."""
    return DocumentVersionModel(
        id=entity.id,
        tenant_id=entity.tenant_id,
        document_id=entity.document_id,
        version_number=entity.version_number,
        status=entity.status.value,
        mime_type=entity.mime_type,
        byte_size=entity.byte_size,
        content_sha256=entity.content_sha256,
        storage_key=entity.storage_key,
        pipeline_version=entity.pipeline_version,
        chunk_count=entity.chunk_count,
        public_error_code=entity.public_error_code,
        progress=entity.progress,
        indexed_at=entity.indexed_at,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )


def ingestion_job_to_domain(model: IngestionJobModel) -> IngestionJob:
    """Mapea una fila de `ingestion_jobs` a entidad de dominio."""
    return IngestionJob(
        id=model.id,
        tenant_id=model.tenant_id,
        document_id=model.document_id,
        document_version_id=model.document_version_id,
        job_type=IngestionJobType(model.job_type),
        status=IngestionJobStatus(model.status),
        created_at=model.created_at,
        updated_at=model.updated_at,
        attempts=model.attempts,
        max_attempts=model.max_attempts,
        available_at=model.available_at,
        lease_owner=model.lease_owner,
        lease_expires_at=model.lease_expires_at,
        progress=model.progress,
        error_code=model.error_code,
        replacement_version_id=model.replacement_version_id,
        started_at=model.started_at,
        finished_at=model.finished_at,
        origin_request_id=model.origin_request_id,
    )


def ingestion_job_to_model(entity: IngestionJob) -> IngestionJobModel:
    """Mapea una entidad `IngestionJob` a fila ORM."""
    return IngestionJobModel(
        id=entity.id,
        tenant_id=entity.tenant_id,
        document_id=entity.document_id,
        document_version_id=entity.document_version_id,
        job_type=entity.job_type.value,
        status=entity.status.value,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
        attempts=entity.attempts,
        max_attempts=entity.max_attempts,
        available_at=entity.created_at if entity.available_at is None else entity.available_at,
        lease_owner=entity.lease_owner,
        lease_expires_at=entity.lease_expires_at,
        progress=entity.progress,
        error_code=entity.error_code,
        replacement_version_id=entity.replacement_version_id,
        started_at=entity.started_at,
        finished_at=entity.finished_at,
        origin_request_id=entity.origin_request_id,
    )


def membership_to_member(model: MembershipModel, email: str) -> TenantMember:
    """Mapea una membresía y el email del usuario a la ficha de empresa."""
    return TenantMember(
        user_id=model.user_id,
        email=email,
        role=TenantRole(model.role),
        status=MembershipStatus(model.status),
        created_at=model.created_at,
        membership_id=model.id,
    )
