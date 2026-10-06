"""Paquetes de exportación de una sola empresa."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from app.persistence.models.lifecycle import ExportPackageModel
from app.persistence.scoped import TenantScopedRepository


class SqlAlchemyExportRepository(TenantScopedRepository):
    """Lee y escribe paquetes solo de la empresa del contexto."""

    def add(
        self,
        *,
        package_id: UUID,
        token_hash: str,
        nonce_hex: str,
        expires_at: datetime,
        storage_key: str,
        created_by: UUID,
        created_at: datetime,
        byte_size: int,
    ) -> None:
        """Inserta el paquete. El token en claro no entra."""
        self._unit.session.add(
            ExportPackageModel(
                id=package_id,
                tenant_id=self.tenant_id,
                token_hash=token_hash,
                nonce_hex=nonce_hex,
                expires_at=expires_at,
                storage_key=storage_key,
                created_by=created_by,
                created_at=created_at,
                byte_size=byte_size,
            )
        )

    async def get(self, package_id: UUID) -> ExportPackageModel | None:
        """Devuelve el paquete de esta empresa, o nada si es de otra."""
        result = await self._unit.session.execute(
            select(ExportPackageModel).where(
                ExportPackageModel.id == package_id,
                ExportPackageModel.tenant_id == self.tenant_id,
            )
        )
        return result.scalar_one_or_none()
