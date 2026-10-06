"""Borra los puntos de una empresa en todas las colecciones."""

from __future__ import annotations

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import FieldCondition, Filter, MatchValue

from app.domain.entities import TenantContext


class QdrantTenantPoints:
    """No depende de la dimensión: recorre las colecciones existentes."""

    def __init__(self, client: AsyncQdrantClient) -> None:
        self._client = client

    async def delete_tenant(self, context: TenantContext) -> None:
        """Elimina los puntos cuyo payload es de esta empresa."""
        selector = Filter(
            must=[
                FieldCondition(
                    key="tenant_id",
                    match=MatchValue(value=str(context.tenant_id)),
                )
            ]
        )
        found = await self._client.get_collections()
        for collection in found.collections:
            await self._client.delete(
                collection_name=collection.name,
                points_selector=selector,
                wait=True,
            )
