"""Factories de datos ficticios para pruebas de identidad."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.entities import Membership, Tenant, User
from app.domain.enums import SystemRole, TenantRole, TenantStatus, UserStatus
from app.domain.normalize import normalize_email, normalize_slug


def utc_now() -> datetime:
    """Instante UTC timezone-aware para fixtures."""
    return datetime.now(UTC)


def make_tenant(
    *,
    tenant_id: UUID | None = None,
    name: str = "Acme Ficticia SA",
    slug: str = "acme-ficticia",
    status: TenantStatus = TenantStatus.ACTIVE,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> Tenant:
    """Empresa de ejemplo con datos inventados."""
    stamp = created_at if created_at is not None else utc_now()
    return Tenant(
        id=tenant_id if tenant_id is not None else uuid4(),
        name=name,
        slug=normalize_slug(slug),
        status=status,
        created_at=stamp,
        updated_at=updated_at if updated_at is not None else stamp,
    )


def make_user(
    *,
    user_id: UUID | None = None,
    email: str = "ana.ejemplo@example.com",
    password_hash: str = "opaque-hash-not-a-secret",
    status: UserStatus = UserStatus.ACTIVE,
    system_role: SystemRole | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> User:
    """Usuario de ejemplo con email de example.com."""
    stamp = created_at if created_at is not None else utc_now()
    return User(
        id=user_id if user_id is not None else uuid4(),
        email=normalize_email(email),
        password_hash=password_hash,
        status=status,
        system_role=system_role,
        created_at=stamp,
        updated_at=updated_at if updated_at is not None else stamp,
    )


def make_membership(
    *,
    membership_id: UUID | None = None,
    tenant_id: UUID | None = None,
    user_id: UUID | None = None,
    role: TenantRole = TenantRole.MEMBER,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> Membership:
    """Membresía de ejemplo entre empresa y usuario ficticios."""
    stamp = created_at if created_at is not None else utc_now()
    return Membership(
        id=membership_id if membership_id is not None else uuid4(),
        tenant_id=tenant_id if tenant_id is not None else uuid4(),
        user_id=user_id if user_id is not None else uuid4(),
        role=role,
        created_at=stamp,
        updated_at=updated_at if updated_at is not None else stamp,
    )
