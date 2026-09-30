"""Base de repositorios que no pueden consultar fuera de su empresa."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.domain.entities import TenantContext
from app.domain.errors import InvalidStateError

if TYPE_CHECKING:
    from app.persistence.unit_of_work import UnitOfWork


class TenantScopedRepository:
    """Exige `TenantContext` y expone solo el tenant de ese contexto."""

    def __init__(self, unit_of_work: UnitOfWork, context: TenantContext) -> None:
        if not isinstance(context, TenantContext):
            message = "El repositorio empresarial exige un TenantContext"
            raise InvalidStateError(message)
        self._unit = unit_of_work
        self._context = context

    @property
    def tenant_id(self) -> UUID:
        """Empresa fija del contexto. No se sustituye por un valor del cliente."""
        return self._context.tenant_id
