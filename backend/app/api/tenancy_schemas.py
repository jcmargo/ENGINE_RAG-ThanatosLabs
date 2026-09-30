"""Esquemas HTTP de empresas y usuarios. Sin `tenant_id` en las rutas empresariales."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _IgnoreExtra(BaseModel):
    """Ignora un `tenant_id` enviado por el cliente."""

    model_config = ConfigDict(extra="ignore")


class CreateTenantRequest(_IgnoreExtra):
    """Cuerpo de alta de empresa."""

    name: str = Field(min_length=1)
    slug: str = Field(min_length=1)
    admin_email: str = Field(min_length=1)


class TenantResponse(BaseModel):
    """Empresa administrada por el proveedor. No incluye el token."""

    id: UUID
    name: str
    slug: str
    status: str
    created_at: datetime
    updated_at: datetime


class TenantCreatedResponse(TenantResponse):
    """Alta: el token de activación aparece solo aquí."""

    admin_user_id: UUID
    activation_token: str


class TenantListResponse(BaseModel):
    """Página de empresas."""

    items: list[TenantResponse]
    next_cursor: str | None
    total: int


class InviteUserRequest(_IgnoreExtra):
    """Invitación dentro de la empresa autenticada."""

    email: str = Field(min_length=1)
    role: str = Field(min_length=1)


class InvitationResponse(BaseModel):
    """Token de activación, una sola vez."""

    user_id: UUID
    email: str
    role: str
    activation_token: str


class TenantUserResponse(BaseModel):
    """Usuario de la empresa autenticada."""

    user_id: UUID
    email: str
    role: str
    status: str
    created_at: datetime


class TenantUserListResponse(BaseModel):
    """Página de usuarios de la empresa autenticada."""

    items: list[TenantUserResponse]
    next_cursor: str | None
    total: int


class ChangeRoleRequest(_IgnoreExtra):
    """Cambio de rol dentro de la empresa autenticada."""

    role: str = Field(min_length=1)


class ActivateRequest(_IgnoreExtra):
    """Activación pública. El token no se registra en claro."""

    token: str = Field(min_length=1)
    password: str = Field(min_length=1)
