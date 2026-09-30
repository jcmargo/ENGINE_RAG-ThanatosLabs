"""Servicio de autenticación y autorización. Fuera de FastAPI."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.entities import ApiKey, AuthPrincipal, AuthSession, User
from app.domain.enums import (
    ALLOWED_API_KEY_SCOPES,
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
from app.domain.errors import (
    AuthenticationError,
    AuthorizationError,
    InvalidStateError,
    NotFoundError,
)
from app.domain.normalize import normalize_email, normalize_slug
from app.infrastructure.jwt_tokens import Clock, JwtAccessTokenService, SystemClock
from app.infrastructure.passwords import PasswordHasher
from app.infrastructure.secrets import (
    API_KEY_PREFIX_MARKER,
    extract_api_key_prefix,
    generate_api_key_material,
    generate_opaque_token,
    hash_opaque_secret,
    secrets_match,
)
from app.persistence.unit_of_work import UnitOfWork

IdFactory = Callable[[], UUID]


@dataclass(frozen=True)
class LoginResult:
    """Resultado de login o refresh: access en JSON, refresh solo para cookie."""

    access_token: str
    refresh_token: str
    expires_at: datetime
    principal: AuthPrincipal
    session_id: UUID


@dataclass(frozen=True)
class CreatedApiKey:
    """API key creada: el secreto completo solo existe aquí."""

    api_key: ApiKey
    secret: str


class AuthService:
    """Login, refresh, logout, resolución de Bearer y gestión de API keys."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        password_hasher: PasswordHasher,
        jwt_service: JwtAccessTokenService,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
        refresh_lifetime: timedelta = timedelta(days=7),
    ) -> None:
        self._session_factory = session_factory
        self._passwords = password_hasher
        self._jwt = jwt_service
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._id_factory: IdFactory = id_factory if id_factory is not None else uuid4
        self._refresh_lifetime = refresh_lifetime

    async def login(
        self,
        *,
        email: str,
        password: str,
        tenant_slug: str | None = None,
    ) -> LoginResult:
        """Autentica usuario y emite access + refresh. Fallos genéricos."""
        normalized_email = normalize_email(email)
        async with UnitOfWork(self._session_factory) as uow:
            user = await self._load_user_for_login(uow, normalized_email)
            password_ok = self._passwords.verify_or_dummy(
                user.password_hash if user is not None else None,
                password,
            )
            if user is None or not password_ok:
                raise AuthenticationError

            if user.status is not UserStatus.ACTIVE:
                raise AuthenticationError

            if self._passwords.needs_rehash(user.password_hash):
                await uow.users.update_password_hash(
                    user.id,
                    self._passwords.hash(password),
                )

            if user.system_role is SystemRole.PROVIDER_ADMIN:
                # Un provider admin no entra por el flujo de empresa aunque envíe slug.
                principal = AuthPrincipal(
                    type=PrincipalType.PROVIDER_ADMIN,
                    user_id=user.id,
                    tenant_id=None,
                    role=None,
                    scopes=(),
                )
                result = await self._issue_session(uow, user=user, principal=principal)
                await uow.commit()
                return result

            if tenant_slug is None or not tenant_slug.strip():
                raise AuthenticationError

            try:
                tenant = await uow.tenants.get_by_slug(normalize_slug(tenant_slug))
            except NotFoundError as error:
                raise AuthenticationError from error
            if tenant.status is not TenantStatus.ACTIVE:
                raise AuthenticationError

            try:
                membership = await uow.memberships.get_by_tenant_and_user(
                    tenant.id,
                    user.id,
                )
            except NotFoundError as error:
                raise AuthenticationError from error
            if membership.status is not MembershipStatus.ACTIVE:
                raise AuthenticationError

            principal_type = (
                PrincipalType.TENANT_ADMIN
                if membership.role is TenantRole.TENANT_ADMIN
                else PrincipalType.MEMBER
            )
            principal = AuthPrincipal(
                type=principal_type,
                user_id=user.id,
                tenant_id=tenant.id,
                role=membership.role,
                scopes=(),
            )
            result = await self._issue_session(
                uow,
                user=user,
                principal=principal,
                tenant_id=tenant.id,
            )
            await uow.commit()
            return result

    async def refresh(self, refresh_token: str) -> LoginResult:
        """Rota la sesión: revoca la presentada y emite otra."""
        token_hash = hash_opaque_secret(refresh_token)
        async with UnitOfWork(self._session_factory) as uow:
            try:
                session = await uow.auth_sessions.get_by_refresh_token_hash(token_hash)
            except NotFoundError as error:
                raise AuthenticationError from error

            now = self._clock.now()
            if session.status is not AuthSessionStatus.ACTIVE or session.expires_at <= now:
                await self._reject_reuse(uow, session)
                await uow.commit()
                raise AuthenticationError

            try:
                user = await uow.users.get_by_id(session.user_id)
            except NotFoundError as error:
                await uow.auth_sessions.mark_revoked(session.id)
                await uow.commit()
                raise AuthenticationError from error

            if user.status is not UserStatus.ACTIVE:
                await uow.auth_sessions.mark_revoked(session.id)
                await uow.commit()
                raise AuthenticationError

            principal = await self._principal_from_user_session(
                uow,
                user=user,
                tenant_id=session.tenant_id,
            )

            new_refresh = generate_opaque_token()
            new_session = AuthSession(
                id=self._id_factory(),
                user_id=user.id,
                tenant_id=session.tenant_id,
                refresh_token_hash=hash_opaque_secret(new_refresh),
                status=AuthSessionStatus.ACTIVE,
                created_at=now,
                updated_at=now,
                expires_at=now + self._refresh_lifetime,
            )
            await uow.auth_sessions.add(new_session)
            await uow.auth_sessions.mark_rotated(
                session.id,
                replaced_by_session_id=new_session.id,
            )

            access_token, expires_at = self._jwt.issue(
                subject=user.id,
                tenant_id=session.tenant_id,
            )
            await uow.commit()
            return LoginResult(
                access_token=access_token,
                refresh_token=new_refresh,
                expires_at=expires_at,
                principal=principal,
                session_id=new_session.id,
            )

    async def logout(self, refresh_token: str | None) -> None:
        """Revoca la sesión asociada al refresh si existe."""
        if refresh_token is None or not refresh_token:
            return
        token_hash = hash_opaque_secret(refresh_token)
        async with UnitOfWork(self._session_factory) as uow:
            try:
                session = await uow.auth_sessions.get_by_refresh_token_hash(token_hash)
            except NotFoundError:
                return
            if session.status is AuthSessionStatus.ACTIVE:
                await uow.auth_sessions.mark_revoked(session.id)
                await uow.commit()

    async def resolve_bearer(self, authorization: str | None) -> AuthPrincipal:
        """Resuelve Bearer JWT o API key y recarga estado desde PostgreSQL."""
        token = self._extract_bearer(authorization)
        if token.startswith(API_KEY_PREFIX_MARKER):
            return await self._resolve_api_key(token)
        return await self._resolve_jwt(token)

    async def create_api_key(
        self,
        *,
        tenant_id: UUID,
        scopes: list[str],
    ) -> CreatedApiKey:
        """Crea una API key. El secreto completo solo se devuelve aquí."""
        parsed = parse_api_key_scopes(scopes)
        secret, prefix, secret_hash = generate_api_key_material()
        now = self._clock.now()
        entity = ApiKey(
            id=self._id_factory(),
            tenant_id=tenant_id,
            prefix=prefix,
            secret_hash=secret_hash,
            status=ApiKeyStatus.ACTIVE,
            scopes=parsed,
            created_at=now,
            updated_at=now,
        )
        async with UnitOfWork(self._session_factory) as uow:
            tenant = await uow.tenants.get_by_id(tenant_id)
            if tenant.status is not TenantStatus.ACTIVE:
                message = "La empresa no está activa"
                raise InvalidStateError(message)
            stored = await uow.api_keys.add(entity)
            await uow.commit()
        return CreatedApiKey(api_key=stored, secret=secret)

    async def list_api_keys(self, *, tenant_id: UUID) -> list[ApiKey]:
        """Lista API keys de la empresa sin secretos."""
        async with UnitOfWork(self._session_factory) as uow:
            return await uow.api_keys.list_by_tenant(tenant_id)

    async def revoke_api_key(self, *, tenant_id: UUID, api_key_id: UUID) -> ApiKey:
        """Revoca una API key del tenant autenticado."""
        async with UnitOfWork(self._session_factory) as uow:
            api_key = await uow.api_keys.get_by_id(api_key_id)
            if api_key.tenant_id != tenant_id:
                raise NotFoundError(f"API key no encontrada: {api_key_id}")
            if api_key.status is ApiKeyStatus.REVOKED:
                return api_key
            revoked = await uow.api_keys.mark_revoked(api_key_id, self._clock.now())
            await uow.commit()
            return revoked

    async def _issue_session(
        self,
        uow: UnitOfWork,
        *,
        user: User,
        principal: AuthPrincipal,
        tenant_id: UUID | None = None,
    ) -> LoginResult:
        now = self._clock.now()
        refresh_token = generate_opaque_token()
        session = AuthSession(
            id=self._id_factory(),
            user_id=user.id,
            tenant_id=tenant_id,
            refresh_token_hash=hash_opaque_secret(refresh_token),
            status=AuthSessionStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            expires_at=now + self._refresh_lifetime,
        )
        await uow.auth_sessions.add(session)
        access_token, expires_at = self._jwt.issue(subject=user.id, tenant_id=tenant_id)
        return LoginResult(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            principal=principal,
            session_id=session.id,
        )

    async def _load_user_for_login(self, uow: UnitOfWork, email: str) -> User | None:
        try:
            return await uow.users.get_by_email(email)
        except NotFoundError:
            return None

    async def _reject_reuse(self, uow: UnitOfWork, session: AuthSession) -> None:
        """Rechaza un refresh rotado/revocado y revoca la sucesora si existe."""
        if session.replaced_by_session_id is not None:
            with contextlib.suppress(NotFoundError):
                await uow.auth_sessions.mark_revoked(session.replaced_by_session_id)
        if session.status is not AuthSessionStatus.REVOKED:
            await uow.auth_sessions.mark_revoked(session.id)

    async def _principal_from_user_session(
        self,
        uow: UnitOfWork,
        *,
        user: User,
        tenant_id: UUID | None,
    ) -> AuthPrincipal:
        if user.system_role is SystemRole.PROVIDER_ADMIN:
            return AuthPrincipal(
                type=PrincipalType.PROVIDER_ADMIN,
                user_id=user.id,
                tenant_id=None,
                role=None,
                scopes=(),
            )
        if tenant_id is None:
            raise AuthenticationError
        try:
            tenant = await uow.tenants.get_by_id(tenant_id)
        except NotFoundError as error:
            raise AuthenticationError from error
        if tenant.status is not TenantStatus.ACTIVE:
            raise AuthenticationError
        try:
            membership = await uow.memberships.get_by_tenant_and_user(tenant_id, user.id)
        except NotFoundError as error:
            raise AuthenticationError from error
        if membership.status is not MembershipStatus.ACTIVE:
            raise AuthenticationError
        principal_type = (
            PrincipalType.TENANT_ADMIN
            if membership.role is TenantRole.TENANT_ADMIN
            else PrincipalType.MEMBER
        )
        return AuthPrincipal(
            type=principal_type,
            user_id=user.id,
            tenant_id=tenant.id,
            role=membership.role,
            scopes=(),
        )

    async def _resolve_jwt(self, token: str) -> AuthPrincipal:
        claims = self._jwt.decode(token)
        async with UnitOfWork(self._session_factory) as uow:
            try:
                user = await uow.users.get_by_id(claims.subject)
            except NotFoundError as error:
                raise AuthenticationError from error
            if user.status is not UserStatus.ACTIVE:
                raise AuthenticationError
            return await self._principal_from_user_session(
                uow,
                user=user,
                tenant_id=claims.tenant_id,
            )

    async def _resolve_api_key(self, token: str) -> AuthPrincipal:
        try:
            prefix = extract_api_key_prefix(token)
        except ValueError as error:
            raise AuthenticationError from error
        async with UnitOfWork(self._session_factory) as uow:
            try:
                api_key = await uow.api_keys.get_by_prefix(prefix)
            except NotFoundError as error:
                raise AuthenticationError from error
            if api_key.status is not ApiKeyStatus.ACTIVE:
                raise AuthenticationError
            if not secrets_match(api_key.secret_hash, hash_opaque_secret(token)):
                raise AuthenticationError
            try:
                tenant = await uow.tenants.get_by_id(api_key.tenant_id)
            except NotFoundError as error:
                raise AuthenticationError from error
            if tenant.status is not TenantStatus.ACTIVE:
                raise AuthenticationError
            return AuthPrincipal(
                type=PrincipalType.API_KEY,
                user_id=None,
                tenant_id=tenant.id,
                role=None,
                scopes=api_key.scopes,
            )

    @staticmethod
    def _extract_bearer(authorization: str | None) -> str:
        if authorization is None:
            raise AuthenticationError
        scheme, _, credentials = authorization.partition(" ")
        if scheme.lower() != "bearer" or not credentials.strip():
            raise AuthenticationError
        return credentials.strip()


def parse_api_key_scopes(raw: list[str]) -> tuple[ApiKeyScope, ...]:
    """Valida scopes permitidos y rechaza desconocidos."""
    if not raw:
        message = "Se requiere al menos un scope"
        raise InvalidStateError(message)
    parsed: list[ApiKeyScope] = []
    seen: set[ApiKeyScope] = set()
    for value in raw:
        try:
            scope = ApiKeyScope(value)
        except ValueError as error:
            message = f"Scope desconocido: {value}"
            raise InvalidStateError(message) from error
        if scope not in ALLOWED_API_KEY_SCOPES:
            message = f"Scope desconocido: {value}"
            raise InvalidStateError(message)
        if scope not in seen:
            seen.add(scope)
            parsed.append(scope)
    return tuple(parsed)


def require_principal_type(
    principal: AuthPrincipal,
    *allowed: PrincipalType,
) -> AuthPrincipal:
    """Exige que el principal tenga uno de los tipos permitidos."""
    if principal.type not in allowed:
        raise AuthorizationError
    return principal


def require_principal_scope(principal: AuthPrincipal, scope: ApiKeyScope) -> AuthPrincipal:
    """Exige que el principal declare el scope pedido."""
    if scope not in principal.scopes:
        raise AuthorizationError
    return principal


class UnitOfWorkFactory(Protocol):
    """Fábrica tipada de unidades de trabajo (documentación)."""

    def __call__(self) -> UnitOfWork: ...
