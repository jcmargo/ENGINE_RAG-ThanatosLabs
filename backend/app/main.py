"""Fábrica ASGI de Engine RAG."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.api.api_keys import router as api_keys_router
from app.api.auth import router as auth_router
from app.api.http import (
    authentication_error_response,
    authorization_error_response,
    conflict_error_response,
    invalid_state_error_response,
    not_found_error_response,
)
from app.api.provider_tenants import router as provider_tenants_router
from app.api.users import router as users_router
from app.auth.service import AuthService
from app.config import (
    Settings,
    get_settings,
    validate_jwt_settings_for_environment,
)
from app.domain.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    InvalidStateError,
    NotFoundError,
)
from app.infrastructure.database import (
    create_database_engine,
    create_session_factory,
    postgres_is_ready,
)
from app.infrastructure.jwt_tokens import Clock, JwtAccessTokenService, SystemClock
from app.infrastructure.passwords import PasswordHasher
from app.infrastructure.vector_client import create_qdrant_client, qdrant_is_ready
from app.tenancy.service import TenantDirectoryService, TenantUserService

_HEALTHZ_BODY = b'{"status":"ok"}'
ComponentCheck = Callable[[], Awaitable[bool]]


def create_app(
    settings: Settings | None = None,
    *,
    postgres_check: ComponentCheck | None = None,
    qdrant_check: ComponentCheck | None = None,
    clock: Clock | None = None,
    password_hasher: PasswordHasher | None = None,
) -> FastAPI:
    """Crea la aplicación. `/healthz` solo indica que el proceso está vivo."""
    if (postgres_check is None) != (qdrant_check is None):
        message = "Las comprobaciones de readiness se sustituyen juntas"
        raise ValueError(message)
    resolved_settings = get_settings() if settings is None else settings
    validate_jwt_settings_for_environment(resolved_settings)
    resolved_clock: Clock = clock if clock is not None else SystemClock()
    resolved_hasher = password_hasher if password_hasher is not None else PasswordHasher()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        logging.getLogger("app").setLevel(resolved_settings.log_level)
        engine = create_database_engine(resolved_settings.database_url)
        session_factory = create_session_factory(engine)
        application.state.session_factory = session_factory
        application.state.settings = resolved_settings
        application.state.qdrant_client = create_qdrant_client(resolved_settings.qdrant_url)
        jwt_service = JwtAccessTokenService(
            secret=resolved_settings.jwt_secret,
            issuer=resolved_settings.jwt_issuer,
            audience=resolved_settings.jwt_audience,
            lifetime=timedelta(minutes=resolved_settings.access_token_minutes),
            clock=resolved_clock,
        )
        application.state.auth_service = AuthService(
            session_factory,
            password_hasher=resolved_hasher,
            jwt_service=jwt_service,
            clock=resolved_clock,
            refresh_lifetime=timedelta(days=resolved_settings.refresh_token_days),
        )
        application.state.tenant_directory = TenantDirectoryService(
            session_factory,
            clock=resolved_clock,
        )
        application.state.tenant_users = TenantUserService(
            session_factory,
            password_hasher=resolved_hasher,
            clock=resolved_clock,
        )
        try:
            yield
        finally:
            await application.state.qdrant_client.close()
            await engine.dispose()

    application = FastAPI(title="Engine RAG", version="0.1.0", lifespan=lifespan)
    application.state.settings = resolved_settings

    @application.exception_handler(AuthenticationError)
    async def handle_authentication_error(
        _request: Request,
        exc: AuthenticationError,
    ) -> JSONResponse:
        return authentication_error_response(exc)

    @application.exception_handler(AuthorizationError)
    async def handle_authorization_error(
        _request: Request,
        exc: AuthorizationError,
    ) -> JSONResponse:
        return authorization_error_response(exc)

    @application.exception_handler(NotFoundError)
    async def handle_not_found_error(_request: Request, exc: NotFoundError) -> JSONResponse:
        return not_found_error_response(exc)

    @application.exception_handler(ConflictError)
    async def handle_conflict_error(_request: Request, exc: ConflictError) -> JSONResponse:
        return conflict_error_response(exc)

    @application.exception_handler(InvalidStateError)
    async def handle_invalid_state_error(
        _request: Request,
        exc: InvalidStateError,
    ) -> JSONResponse:
        return invalid_state_error_response(exc)

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

    application.include_router(auth_router)
    application.include_router(api_keys_router)
    application.include_router(provider_tenants_router)
    application.include_router(users_router)
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
