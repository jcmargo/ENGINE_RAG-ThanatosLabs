"""Dependencias FastAPI de autenticación y RBAC."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.service import AuthService, require_principal_scope, require_principal_type
from app.domain.entities import AuthPrincipal, TenantContext
from app.domain.enums import ApiKeyScope, PrincipalType
from app.domain.errors import AuthenticationError, AuthorizationError

_bearer_scheme = HTTPBearer(auto_error=False)

AuthDependency = Callable[..., Awaitable[AuthPrincipal]]


def get_auth_service(request: Request) -> AuthService:
    """Obtiene el AuthService montado en el lifespan."""
    service = getattr(request.app.state, "auth_service", None)
    if not isinstance(service, AuthService):
        message = "AuthService no está configurado en la aplicación"
        raise RuntimeError(message)
    return service


async def require_authenticated(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthPrincipal:
    """Exige un Bearer válido y reconstruye el principal desde PostgreSQL."""
    authorization = request.headers.get("Authorization")
    if credentials is not None:
        authorization = f"{credentials.scheme} {credentials.credentials}"
    principal = await auth_service.resolve_bearer(authorization)
    request.state.principal = principal
    return principal


async def require_provider_admin(
    principal: Annotated[AuthPrincipal, Depends(require_authenticated)],
) -> AuthPrincipal:
    """Exige un provider admin global."""
    return require_principal_type(principal, PrincipalType.PROVIDER_ADMIN)


async def require_tenant_admin(
    principal: Annotated[AuthPrincipal, Depends(require_authenticated)],
) -> AuthPrincipal:
    """Exige un tenant_admin con empresa en el principal."""
    require_principal_type(principal, PrincipalType.TENANT_ADMIN)
    if principal.tenant_id is None:
        raise AuthorizationError
    return principal


def build_tenant_context(principal: AuthPrincipal) -> TenantContext:
    """Construye el alcance de empresa a partir del principal ya validado."""
    if principal.tenant_id is None:
        raise AuthorizationError
    return TenantContext(tenant_id=principal.tenant_id, principal=principal)


async def require_tenant_context(
    principal: Annotated[AuthPrincipal, Depends(require_tenant_admin)],
) -> TenantContext:
    """Exige un tenant admin y entrega el contexto de su empresa."""
    return build_tenant_context(principal)


async def require_documents_read(
    principal: Annotated[AuthPrincipal, Depends(require_authenticated)],
) -> TenantContext:
    """Lectura de documentos: tenant admin o API key con `documents:read`."""
    return _document_context(principal, ApiKeyScope.DOCUMENTS_READ)


async def require_documents_write(
    principal: Annotated[AuthPrincipal, Depends(require_authenticated)],
) -> TenantContext:
    """Escritura de documentos: tenant admin o API key con `documents:write`."""
    return _document_context(principal, ApiKeyScope.DOCUMENTS_WRITE)


def _document_context(principal: AuthPrincipal, scope: ApiKeyScope) -> TenantContext:
    if principal.type is PrincipalType.TENANT_ADMIN:
        return build_tenant_context(principal)
    if principal.type is PrincipalType.API_KEY:
        require_principal_scope(principal, scope)
        return build_tenant_context(principal)
    raise AuthorizationError


async def require_query_context(
    principal: Annotated[AuthPrincipal, Depends(require_authenticated)],
) -> TenantContext:
    """Preguntas: usuario de empresa activo o API key con scope `query`."""
    if principal.type in {PrincipalType.TENANT_ADMIN, PrincipalType.MEMBER}:
        return build_tenant_context(principal)
    if principal.type is PrincipalType.API_KEY:
        require_principal_scope(principal, ApiKeyScope.QUERY)
        return build_tenant_context(principal)
    raise AuthorizationError


def require_scope(scope: str) -> AuthDependency:
    """Exige un scope concreto en el principal autenticado."""

    try:
        required = ApiKeyScope(scope)
    except ValueError as error:
        message = f"Scope desconocido: {scope}"
        raise InvalidScopeDefinitionError(message) from error

    async def _dependency(
        principal: Annotated[AuthPrincipal, Depends(require_authenticated)],
    ) -> AuthPrincipal:
        return require_principal_scope(principal, required)

    return _dependency


class InvalidScopeDefinitionError(ValueError):
    """Scope pasado a `require_scope` que no está en la lista permitida."""


__all__ = [
    "AuthenticationError",
    "AuthorizationError",
    "InvalidScopeDefinitionError",
    "get_auth_service",
    "require_authenticated",
    "require_documents_read",
    "require_documents_write",
    "require_provider_admin",
    "require_query_context",
    "require_scope",
    "require_tenant_admin",
    "require_tenant_context",
]
