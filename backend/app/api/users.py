"""Rutas empresariales de usuarios. El tenant sale de `TenantContext`."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import require_tenant_context
from app.api.tenancy_schemas import (
    ActivateRequest,
    ChangeRoleRequest,
    InvitationResponse,
    InviteUserRequest,
    TenantUserListResponse,
    TenantUserResponse,
)
from app.domain.entities import TenantContext, TenantMember
from app.domain.enums import TenantRole
from app.domain.errors import InvalidStateError
from app.tenancy.service import TenantUserService, parse_membership_status

router = APIRouter(tags=["users"])


def get_tenant_users(request: Request) -> TenantUserService:
    """Servicio de usuarios montado en el lifespan."""
    service = getattr(request.app.state, "tenant_users", None)
    if not isinstance(service, TenantUserService):
        message = "TenantUserService no está configurado"
        raise RuntimeError(message)
    return service


@router.post("/v1/auth/activate", status_code=204)
async def activate(
    body: ActivateRequest,
    users: Annotated[TenantUserService, Depends(get_tenant_users)],
) -> None:
    """Fija la contraseña y consume el token. No revela si el token existía."""
    await users.activate(token=body.token, password=body.password)


@router.get("/v1/users", response_model=TenantUserListResponse)
async def list_users(
    context: Annotated[TenantContext, Depends(require_tenant_context)],
    users: Annotated[TenantUserService, Depends(get_tenant_users)],
    status: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> TenantUserListResponse:
    """Lista los usuarios de la empresa autenticada."""
    page = await users.list_members(
        context,
        status=parse_membership_status(status),
        cursor=cursor,
        limit=limit,
    )
    return TenantUserListResponse(
        items=[_user_response(item) for item in page.items],
        next_cursor=page.next_cursor,
        total=page.total,
    )


@router.post("/v1/users", response_model=InvitationResponse, status_code=201)
async def invite_user(
    body: InviteUserRequest,
    context: Annotated[TenantContext, Depends(require_tenant_context)],
    users: Annotated[TenantUserService, Depends(get_tenant_users)],
) -> InvitationResponse:
    """Invita a un usuario de esta empresa y devuelve el token una sola vez."""
    created = await users.invite(context, email=body.email, role=_role(body.role))
    return InvitationResponse(
        user_id=created.user_id,
        email=created.email,
        role=created.role.value,
        activation_token=created.activation_token,
    )


@router.get("/v1/users/{user_id}", response_model=TenantUserResponse)
async def get_user(
    user_id: UUID,
    context: Annotated[TenantContext, Depends(require_tenant_context)],
    users: Annotated[TenantUserService, Depends(get_tenant_users)],
) -> TenantUserResponse:
    """Ficha de un usuario de esta empresa."""
    member = await users.get_member(context, user_id)
    return _user_response(member)


@router.patch("/v1/users/{user_id}", response_model=TenantUserResponse)
async def change_role(
    user_id: UUID,
    body: ChangeRoleRequest,
    context: Annotated[TenantContext, Depends(require_tenant_context)],
    users: Annotated[TenantUserService, Depends(get_tenant_users)],
) -> TenantUserResponse:
    """Cambia el rol dentro de esta empresa."""
    member = await users.change_role(context, user_id, _role(body.role))
    return _user_response(member)


@router.post("/v1/users/{user_id}/deactivate", response_model=TenantUserResponse)
async def deactivate_user(
    user_id: UUID,
    context: Annotated[TenantContext, Depends(require_tenant_context)],
    users: Annotated[TenantUserService, Depends(get_tenant_users)],
) -> TenantUserResponse:
    """Desactiva la membresía dentro de esta empresa."""
    member = await users.deactivate(context, user_id)
    return _user_response(member)


def _role(value: str) -> TenantRole:
    try:
        return TenantRole(value)
    except ValueError as error:
        message = "Rol de empresa desconocido"
        raise InvalidStateError(message) from error


def _user_response(member: TenantMember) -> TenantUserResponse:
    return TenantUserResponse(
        user_id=member.user_id,
        email=member.email,
        role=member.role.value,
        status=member.status.value,
        created_at=member.created_at,
    )
