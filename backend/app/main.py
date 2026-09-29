"""Fábrica ASGI de Engine RAG."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import Response

from app.config import Settings, get_settings

_HEALTHZ_BODY = b'{"status":"ok"}'


def create_app(settings: Settings | None = None) -> FastAPI:
    """Crea la aplicación. `/healthz` solo indica que el proceso está vivo."""
    resolved_settings = get_settings() if settings is None else settings

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        logging.getLogger("app").setLevel(resolved_settings.log_level)
        yield

    application = FastAPI(title="Engine RAG", version="0.1.0", lifespan=lifespan)

    @application.get("/healthz", summary="Comprueba que el proceso está vivo")
    def healthz() -> Response:
        return Response(content=_HEALTHZ_BODY, media_type="application/json")

    return application


app = create_app()
