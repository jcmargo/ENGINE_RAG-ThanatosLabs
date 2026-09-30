"""Conversión explícita entre modelos ORM y entidades de dominio."""

from app.domain.entities import (
    ActivationToken,
    ApiKey,
    AuthSession,
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
