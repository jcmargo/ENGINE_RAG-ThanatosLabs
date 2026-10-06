"""Sonda de Ollama para `/readyz`. No registra la URL."""

import logging

import httpx

logger = logging.getLogger(__name__)


async def ollama_is_ready(
    base_url: str,
    *,
    timeout_seconds: float = 2.0,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Comprueba `GET /api/tags`. Cualquier fallo deja el componente no disponible."""
    if timeout_seconds <= 0:
        message = "El timeout de Ollama debe ser positivo"
        raise ValueError(message)
    if not base_url.strip():
        logger.info("readyz component=ollama status=unavailable")
        return False
    owns_client = client is None
    http = client if client is not None else httpx.AsyncClient(timeout=timeout_seconds)
    try:
        response = await http.get(f"{base_url.rstrip('/')}/api/tags")
    except Exception:
        logger.info("readyz component=ollama status=unavailable")
        return False
    finally:
        if owns_client:
            await http.aclose()
    if response.status_code != 200:
        logger.info("readyz component=ollama status=unavailable")
        return False
    return True
