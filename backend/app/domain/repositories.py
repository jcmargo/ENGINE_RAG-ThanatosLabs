"""Contratos de repositorio de identidad. La persistencia los implementa."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.entities import ApiKey, AuthSession, Membership, Tenant, User
from app.domain.enums import TenantStatus, UserStatus


class TenantRepository(Protocol):
    """Consultas y escrituras de empresas."""

    async def get_by_id(self, tenant_id: UUID) -> Tenant:
        """Obtiene una empresa por id o lanza `NotFoundError`."""

    async def get_by_slug(self, slug: str) -> Tenant:
        """Obtiene una empresa por slug normalizado o lanza `NotFoundError`."""

    async def exists_by_slug(self, slug: str) -> bool:
        """Indica si ya existe una empresa con ese slug normalizado."""

    async def add(self, tenant: Tenant) -> Tenant:
        """Persiste una empresa nueva dentro de la unidad de trabajo abierta."""

    async def update_status(self, tenant_id: UUID, status: TenantStatus) -> Tenant:
        """Cambia el estado tras validar la transición de dominio."""


class UserRepository(Protocol):
    """Consultas y escrituras de usuarios globales."""

    async def get_by_id(self, user_id: UUID) -> User:
        """Obtiene un usuario por id o lanza `NotFoundError`."""

    async def get_by_email(self, email: str) -> User:
        """Obtiene un usuario por email normalizado o lanza `NotFoundError`."""

    async def exists_by_email(self, email: str) -> bool:
        """Indica si ya existe un usuario con ese email normalizado."""

    async def exists_provider_admin(self) -> bool:
        """Indica si ya existe al menos un provider admin."""

    async def add(self, user: User) -> User:
        """Persiste un usuario nuevo dentro de la unidad de trabajo abierta."""

    async def update_status(self, user_id: UUID, status: UserStatus) -> User:
        """Cambia el estado tras validar la transición de dominio."""

    async def update_password_hash(self, user_id: UUID, password_hash: str) -> User:
        """Actualiza el hash opaco de contraseña."""


class MembershipRepository(Protocol):
    """Consultas y escrituras de membresías."""

    async def get_by_id(self, membership_id: UUID) -> Membership:
        """Obtiene una membresía por id o lanza `NotFoundError`."""

    async def get_by_tenant_and_user(self, tenant_id: UUID, user_id: UUID) -> Membership:
        """Obtiene la membresía de una pareja empresa-usuario o lanza `NotFoundError`."""

    async def list_by_user(self, user_id: UUID) -> list[Membership]:
        """Lista las membresías de un usuario."""

    async def list_by_tenant(self, tenant_id: UUID) -> list[Membership]:
        """Lista las membresías de una empresa."""

    async def exists(self, tenant_id: UUID, user_id: UUID) -> bool:
        """Indica si ya existe la pareja empresa-usuario."""

    async def add(self, membership: Membership) -> Membership:
        """Persiste una membresía nueva dentro de la unidad de trabajo abierta."""


class AuthSessionRepository(Protocol):
    """Consultas y escrituras de sesiones de refresh."""

    async def get_by_id(self, session_id: UUID) -> AuthSession:
        """Obtiene una sesión por id o lanza `NotFoundError`."""

    async def get_by_refresh_token_hash(self, refresh_token_hash: str) -> AuthSession:
        """Obtiene una sesión por hash de refresh o lanza `NotFoundError`."""

    async def add(self, session: AuthSession) -> AuthSession:
        """Persiste una sesión nueva."""

    async def mark_rotated(
        self,
        session_id: UUID,
        *,
        replaced_by_session_id: UUID,
    ) -> AuthSession:
        """Marca la sesión como rotada y enlaza la sucesora."""

    async def mark_revoked(self, session_id: UUID) -> AuthSession:
        """Marca la sesión como revocada."""


class ApiKeyRepository(Protocol):
    """Consultas y escrituras de API keys."""

    async def get_by_id(self, api_key_id: UUID) -> ApiKey:
        """Obtiene una API key por id o lanza `NotFoundError`."""

    async def get_by_prefix(self, prefix: str) -> ApiKey:
        """Obtiene una API key por prefijo identificable o lanza `NotFoundError`."""

    async def list_by_tenant(self, tenant_id: UUID) -> list[ApiKey]:
        """Lista las API keys de una empresa."""

    async def add(self, api_key: ApiKey) -> ApiKey:
        """Persiste una API key nueva."""

    async def mark_revoked(self, api_key_id: UUID, revoked_at: datetime) -> ApiKey:
        """Marca la API key como revocada."""
