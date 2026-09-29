"""Fábrica ASGI de Engine RAG."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from app.config import Settings, get_settings
from app.infrastructure.database import (
    create_database_engine,
    create_session_factory,
    postgres_is_ready,
)
from app.infrastructure.vector_client import create_qdrant_client, qdrant_is_ready

_HEALTHZ_BODY = b'{"status":"ok"}'
ComponentCheck = Callable[[], Awaitable[bool]]


def create_app(
    settings: Settings | None = None,
    *,
    postgres_check: ComponentCheck | None = None,
    qdrant_check: ComponentCheck | None = None,
) -> FastAPI:
    """Crea la aplicación. `/healthz` solo indica que el proceso está vivo."""
    if (postgres_check is None) != (qdrant_check is None):
        message = "Las comprobaciones de readiness se sustituyen juntas"
        raise ValueError(message)
    resolved_settings = get_settings() if settings is None else settings

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        logging.getLogger("app").setLevel(resolved_settings.log_level)
        engine = create_database_engine(resolved_settings.database_url)
        application.state.session_factory = create_session_factory(engine)
        application.state.qdrant_client = create_qdrant_client(resolved_settings.qdrant_url)
        try:
            yield
        finally:
            await application.state.qdrant_client.close()
            await engine.dispose()

    application = FastAPI(title="Engine RAG", version="0.1.0", lifespan=lifespan)

    @application.get("/healthz", summary="Comprueba que el proceso está vivo")
    def healthz() -> Response:
        return Response(content=_HEALTHZ_BODY, media_type="application/json")

    @application.get("/readyz", summary="Comprueba PostgreSQL y Qdrant")
    async def readyz() -> JSONResponse:
        if postgres_check is not None and qdrant_check is not None:
            postgresql, qdrant = await asyncio.gather(postgres_check(), qdrant_check())
        else:
            postgresql, qdrant = await asyncio.gather(
                postgres_is_ready(application.state.session_factory),
                qdrant_is_ready(application.state.qdrant_client),
            )
        return JSONResponse(
            status_code=200 if postgresql and qdrant else 503,
            content=_ready_payload(postgresql, qdrant),
        )

    return application


def _ready_payload(postgresql: bool, qdrant: bool) -> dict[str, object]:
    ready = postgresql and qdrant
    return {
        "status": "ok" if ready else "unavailable",
        "components": {
            "postgresql": "ok" if postgresql else "unavailable",
            "qdrant": "ok" if qdrant else "unavailable",
        },
    }


app = create_app()
