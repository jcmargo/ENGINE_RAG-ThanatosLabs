"""Alta de empresas, invitación y administración de usuarios."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.entities import (
    ActivationToken,
    Membership,
    Tenant,
    TenantContext,
    TenantMember,
    User,
)
from app.domain.enums import (
    ActivationTokenStatus,
    MembershipStatus,
    TenantRole,
    TenantStatus,
    UserStatus,
)
from app.domain.errors import AuthenticationError, ConflictError, InvalidStateError, NotFoundError
from app.domain.membership_policy import assert_last_tenant_admin_remains
from app.domain.normalize import normalize_email, normalize_slug
from app.domain.pagination import PageCursor, decode_page_cursor, encode_page_cursor
from app.domain.transitions import transition_membership_status
from app.infrastructure.jwt_tokens import Clock, SystemClock
from app.infrastructure.passwords import PasswordHasher
from app.infrastructure.secrets import generate_opaque_token, hash_opaque_secret
from app.persistence.repositories.tenant_users import SqlAlchemyTenantUserRepository
from app.persistence.unit_of_work import UnitOfWork

ACTIVATION_TTL = timedelta(hours=72)
UNUSABLE_PASSWORD_HASH = "!"
_ACTIVATION_FAILURE = "Token de activación inválido"


@dataclass(frozen=True)
class CreatedTenant:
    """Empresa recién creada y el token de activación en claro, una sola vez."""

    tenant: Tenant
    admin_user_id: UUID
    activation_token: str


@dataclass(frozen=True)
class CreatedInvitation:
    """Invitación emitida. El token no se vuelve a leer."""

    user_id: UUID
    email: str
    role: TenantRole
    activation_token: str


@dataclass(frozen=True)
class TenantPage:
    """Página de empresas del proveedor."""

    items: list[Tenant]
    total: int
    next_cursor: str | None


@dataclass(frozen=True)
class MemberPage:
    """Página de usuarios de una empresa."""

    items: list[TenantMember]
    total: int
    next_cursor: str | None


class TenantDirectoryService:
    """Ciclo de vida de empresas. Lo usa solo el provider admin."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clock: Clock | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock: Clock = clock if clock is not None else SystemClock()

    async def create_tenant(self, *, name: str, slug: str, admin_email: str) -> CreatedTenant:
        """Crea empresa, administrador y token de activación en una transacción."""
        now = self._clock.now()
        normalized_slug = normalize_slug(slug)
        email = normalize_email(admin_email)
        tenant = Tenant(
            id=uuid4(),
            name=name.strip(),
            slug=normalized_slug,
            status=TenantStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        async with UnitOfWork(self._session_factory) as uow:
            await uow.tenants.add(tenant)
            user = await _get_or_create_user(uow, email=email, now=now)
            membership = Membership(
                id=uuid4(),
                tenant_id=tenant.id,
                user_id=user.id,
                role=TenantRole.TENANT_ADMIN,
                status=MembershipStatus.DISABLED,
                created_at=now,
                updated_at=now,
            )
            await uow.memberships.add(membership)
            plaintext = await _issue_activation(
                uow,
                tenant_id=tenant.id,
                user_id=user.id,
                now=now,
            )
            await uow.commit()
        return CreatedTenant(
            tenant=tenant,
            admin_user_id=user.id,
            activation_token=plaintext,
        )

    async def list_tenants(
        self,
        *,
        status: TenantStatus | None,
        cursor: str | None,
        limit: int,
    ) -> TenantPage:
        """Lista empresas con cursor estable y filtro de estado."""
        after = _cursor_or_none(cursor)
        async with UnitOfWork(self._session_factory) as uow:
            total = await uow.tenants.count(status=status)
            rows = await uow.tenants.list_page(status=status, after=after, limit=limit + 1)
        return _tenant_page(rows, total=total, limit=limit)

    async def get_tenant(self, tenant_id: UUID) -> Tenant:
        """Ficha de una empresa para el proveedor."""
        async with UnitOfWork(self._session_factory) as uow:
            return await uow.tenants.get_by_id(tenant_id)

    async def suspend(self, tenant_id: UUID) -> Tenant:
        """Suspende la empresa. El acceso cae en la siguiente solicitud."""
        async with UnitOfWork(self._session_factory) as uow:
            tenant = await uow.tenants.update_status(tenant_id, TenantStatus.SUSPENDED)
            await uow.commit()
            return tenant

    async def reactivate(self, tenant_id: UUID) -> Tenant:
        """Reactiva la empresa. Las credenciales no revocadas vuelven a servir."""
        async with UnitOfWork(self._session_factory) as uow:
            tenant = await uow.tenants.update_status(tenant_id, TenantStatus.ACTIVE)
            await uow.commit()
            return tenant


class TenantUserService:
    """Usuarios de una empresa. El tenant sale solo de `TenantContext`."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        password_hasher: PasswordHasher,
        clock: Clock | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._passwords = password_hasher
        self._clock: Clock = clock if clock is not None else SystemClock()

    async def invite(
        self,
        context: TenantContext,
        *,
        email: str,
        role: TenantRole,
    ) -> CreatedInvitation:
        """Invita a un usuario de esta empresa y devuelve el token una sola vez."""
        now = self._clock.now()
        normalized = normalize_email(email)
        async with UnitOfWork(self._session_factory) as uow:
            directory = SqlAlchemyTenantUserRepository(uow, context)
            user = await _get_or_create_user(uow, email=normalized, now=now)
            try:
                current = await directory.get_member(user.id)
            except NotFoundError:
                current = None
            if current is not None and current.status is MembershipStatus.ACTIVE:
                message = "El usuario ya pertenece a la empresa"
                raise ConflictError(message)
            if current is None:
                await directory.add_membership(
                    Membership(
                        id=uuid4(),
                        tenant_id=context.tenant_id,
                        user_id=user.id,
                        role=role,
                        status=MembershipStatus.DISABLED,
                        created_at=now,
                        updated_at=now,
                    )
                )
            elif current.role is not role:
                await directory.update_role(user.id, role)
            plaintext = await _issue_activation(
                uow,
                tenant_id=context.tenant_id,
                user_id=user.id,
                now=now,
            )
            await uow.commit()
        return CreatedInvitation(
            user_id=user.id,
            email=normalized,
            role=role,
            activation_token=plaintext,
        )

    async def list_members(
        self,
        context: TenantContext,
        *,
        status: MembershipStatus | None,
        cursor: str | None,
        limit: int,
    ) -> MemberPage:
        """Lista usuarios de la empresa del contexto."""
        after = _cursor_or_none(cursor)
        async with UnitOfWork(self._session_factory) as uow:
            directory = SqlAlchemyTenantUserRepository(uow, context)
            total = await directory.count_members(status=status)
            rows = await directory.list_members(status=status, after=after, limit=limit + 1)
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1]
            next_cursor = encode_page_cursor(last.created_at, last.membership_id)
        return MemberPage(items=page, total=total, next_cursor=next_cursor)

    async def get_member(self, context: TenantContext, user_id: UUID) -> TenantMember:
        """Ficha de un usuario de esta empresa."""
        async with UnitOfWork(self._session_factory) as uow:
            directory = SqlAlchemyTenantUserRepository(uow, context)
            return await directory.get_member(user_id)

    async def change_role(
        self,
        context: TenantContext,
        user_id: UUID,
        role: TenantRole,
    ) -> TenantMember:
        """Cambia entre `tenant_admin` y `member` dentro de la empresa."""
        async with UnitOfWork(self._session_factory) as uow:
            directory = SqlAlchemyTenantUserRepository(uow, context)
            current = await directory.get_member(user_id)
            if current.role is role:
                return current
            active_admins = await directory.count_active_admins()
            assert_last_tenant_admin_remains(
                target_role=current.role,
                target_status=current.status,
                active_admin_count=active_admins,
                next_role=role,
            )
            updated = await directory.update_role(user_id, role)
            await uow.commit()
            return updated

    async def deactivate(self, context: TenantContext, user_id: UUID) -> TenantMember:
        """Desactiva la membresía. No borra al usuario."""
        async with UnitOfWork(self._session_factory) as uow:
            directory = SqlAlchemyTenantUserRepository(uow, context)
            current = await directory.get_member(user_id)
            if current.status is MembershipStatus.DISABLED:
                return current
            active_admins = await directory.count_active_admins()
            assert_last_tenant_admin_remains(
                target_role=current.role,
                target_status=current.status,
                active_admin_count=active_admins,
                next_status=MembershipStatus.DISABLED,
            )
            transition_membership_status(current.status, MembershipStatus.DISABLED)
            updated = await directory.update_status(user_id, MembershipStatus.DISABLED)
            await uow.commit()
            return updated

    async def activate(self, *, token: str, password: str) -> None:
        """Consume el token, fija la contraseña y activa la membresía."""
        if not password:
            message = "La contraseña no puede estar vacía"
            raise InvalidStateError(message)
        now = self._clock.now()
        token_hash = hash_opaque_secret(token)
        async with UnitOfWork(self._session_factory) as uow:
            try:
                record = await uow.activation_tokens.get_by_hash(token_hash)
            except NotFoundError as error:
                raise AuthenticationError(_ACTIVATION_FAILURE) from error
            if record.status is not ActivationTokenStatus.PENDING or record.expires_at <= now:
                raise AuthenticationError(_ACTIVATION_FAILURE)
            await uow.users.update_password_hash(record.user_id, self._passwords.hash(password))
            try:
                membership = await uow.memberships.get_by_tenant_and_user(
                    record.tenant_id,
                    record.user_id,
                )
            except NotFoundError as error:
                raise AuthenticationError(_ACTIVATION_FAILURE) from error
            if membership.status is not MembershipStatus.ACTIVE:
                await uow.memberships.update_status(membership.id, MembershipStatus.ACTIVE)
            await uow.activation_tokens.mark_consumed(record.id, now)
            await uow.activation_tokens.revoke_pending(
                tenant_id=record.tenant_id,
                user_id=record.user_id,
                except_id=record.id,
                revoked_at=now,
            )
            await uow.commit()


def parse_tenant_status(value: str | None) -> TenantStatus | None:
    """Acepta solo estados de empresa conocidos."""
    if value is None or not value.strip():
        return None
    try:
        return TenantStatus(value)
    except ValueError as error:
        message = "Filtro de estado desconocido"
        raise InvalidStateError(message) from error


def parse_membership_status(value: str | None) -> MembershipStatus | None:
    """Acepta solo estados de membresía conocidos."""
    if value is None or not value.strip():
        return None
    try:
        return MembershipStatus(value)
    except ValueError as error:
        message = "Filtro de estado desconocido"
        raise InvalidStateError(message) from error


def _cursor_or_none(cursor: str | None) -> PageCursor | None:
    if cursor is None or not cursor.strip():
        return None
    return decode_page_cursor(cursor)


def _tenant_page(rows: list[Tenant], *, total: int, limit: int) -> TenantPage:
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1]
        next_cursor = encode_page_cursor(last.created_at, last.id)
    return TenantPage(items=page, total=total, next_cursor=next_cursor)


async def _get_or_create_user(uow: UnitOfWork, *, email: str, now: datetime) -> User:
    try:
        return await uow.users.get_by_email(email)
    except NotFoundError:
        user = User(
            id=uuid4(),
            email=email,
            password_hash=UNUSABLE_PASSWORD_HASH,
            status=UserStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        return await uow.users.add(user)


async def _issue_activation(
    uow: UnitOfWork,
    *,
    tenant_id: UUID,
    user_id: UUID,
    now: datetime,
) -> str:
    plaintext = generate_opaque_token()
    token_id = uuid4()
    await uow.activation_tokens.add(
        ActivationToken(
            id=token_id,
            tenant_id=tenant_id,
            user_id=user_id,
            token_hash=hash_opaque_secret(plaintext),
            status=ActivationTokenStatus.PENDING,
            expires_at=now + ACTIVATION_TTL,
            created_at=now,
            updated_at=now,
        )
    )
    await uow.activation_tokens.revoke_pending(
        tenant_id=tenant_id,
        user_id=user_id,
        except_id=token_id,
        revoked_at=now,
    )
    return plaintext
