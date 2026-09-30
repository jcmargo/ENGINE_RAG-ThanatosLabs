"""Esquemas HTTP de autenticación y API keys."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Cuerpo de `POST /v1/auth/login`."""

    email: str = Field(min_length=1)
    password: str = Field(min_length=1)
    tenant_slug: str | None = None


class AccessTokenResponse(BaseModel):
    """Respuesta de login/refresh. El refresh viaja solo en cookie."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    principal_type: str
    user_id: UUID | None = None
    tenant_id: UUID | None = None


class CreateApiKeyRequest(BaseModel):
    """Cuerpo de creación de API key."""

    scopes: list[str] = Field(min_length=1)


class ApiKeyCreatedResponse(BaseModel):
    """Respuesta de creación: incluye el secreto una sola vez."""

    id: UUID
    prefix: str
    scopes: list[str]
    status: str
    created_at: datetime
    secret: str


class ApiKeyListItem(BaseModel):
    """Elemento de listado sin secreto."""

    id: UUID
    prefix: str
    scopes: list[str]
    status: str
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None = None


class ApiKeyListResponse(BaseModel):
    """Listado de API keys del tenant."""

    items: list[ApiKeyListItem]


class ApiKeyRevokedResponse(BaseModel):
    """Confirmación de revocación."""

    id: UUID
    prefix: str
    status: str
    revoked_at: datetime | None
