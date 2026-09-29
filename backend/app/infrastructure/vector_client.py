"""Cliente HTTP de Qdrant, administrado por el ciclo de vida de la API."""

import asyncio
import logging

from qdrant_client import AsyncQdrantClient

logger = logging.getLogger(__name__)


def create_qdrant_client(qdrant_url: str, timeout_seconds: int = 2) -> AsyncQdrantClient:
    """Crea el cliente sin consultar el servidor."""
    if timeout_seconds < 1:
        message = "El timeout de Qdrant debe ser de al menos un segundo"
        raise ValueError(message)
    return AsyncQdrantClient(
        url=qdrant_url,
        prefer_grpc=False,
        timeout=timeout_seconds,
        check_compatibility=False,
    )


async def qdrant_is_ready(client: AsyncQdrantClient, timeout_seconds: float = 2.0) -> bool:
    """Comprueba el estado de Qdrant con el cliente. No registra la URL ni el error."""
    try:
        async with asyncio.timeout(timeout_seconds):
            info = await client.info()
    except Exception:
        # El mensaje del cliente puede incluir la URL.
        logger.info("readyz component=qdrant status=unavailable")
        return False
    version = info.version
    return isinstance(version, str) and bool(version)
