"""Fábrica ASGI de Engine RAG."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.api_keys import router as api_keys_router
from app.api.auth import router as auth_router
from app.api.conversations import router as conversations_router
from app.api.documents import router as documents_router
from app.api.edge import (
    PUBLIC_FAILURE,
    EdgeMiddleware,
    error_payload,
    metrics_visible,
    provider_admin,
)
from app.api.exports import router as exports_router
from app.api.http import (
    authentication_error_response,
    authorization_error_response,
    conflict_error_response,
    invalid_state_error_response,
    not_found_error_response,
    payload_too_large_response,
    unexpected_error_response,
)
from app.api.provider_tenants import router as provider_tenants_router
from app.api.questions import router as questions_router
from app.api.users import router as users_router
from app.auth.service import AuthService
from app.config import (
    DEFAULT_CORS_ORIGINS,
    Environment,
    LLMProviderName,
    Settings,
    cors_origin_list,
    get_settings,
    resolve_file_store_path,
    validate_jwt_settings_for_environment,
)
from app.documents.service import DocumentService
from app.documents.store import FileStore
from app.domain.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    InvalidStateError,
    NotFoundError,
    PayloadTooLargeError,
)
from app.infrastructure.database import (
    create_database_engine,
    create_session_factory,
    postgres_is_ready,
)
from app.infrastructure.jwt_tokens import Clock, JwtAccessTokenService, SystemClock
from app.infrastructure.local_files import LocalFileStore
from app.infrastructure.metrics import observe_dependency
from app.infrastructure.ollama_ready import ollama_is_ready
from app.infrastructure.passwords import PasswordHasher
from app.infrastructure.rate_limit import MemoryRateLimiter, PostgresRateLimiter, RateLimiter
from app.infrastructure.vector_client import create_qdrant_client, qdrant_is_ready
from app.observability.audit_sink import AuditSink, MemoryAuditSink, PostgresAuditSink
from app.observability.logging import configure_logging
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
    file_store: FileStore | None = None,
    before_document_commit: Callable[[], None] | None = None,
    ollama_check: ComponentCheck | None = None,
    question_loader: Callable[..., object] | None = None,
    rate_limiter: RateLimiter | None = None,
    audit_sink: AuditSink | None = None,
) -> FastAPI:
    """Crea la aplicación. `/healthz` solo indica que el proceso está vivo."""
    resolved_settings = get_settings() if settings is None else settings
    provider_is_ollama = resolved_settings.llm_provider is LLMProviderName.OLLAMA
    _require_ready_checks(
        postgres_check=postgres_check,
        qdrant_check=qdrant_check,
        ollama_check=ollama_check,
        provider_is_ollama=provider_is_ollama,
    )
    validate_jwt_settings_for_environment(resolved_settings)
    resolved_clock: Clock = clock if clock is not None else SystemClock()
    resolved_hasher = password_hasher if password_hasher is not None else PasswordHasher()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging(
            resolved_settings.log_level.value,
            service="engine-rag-api",
            environment=resolved_settings.environment.value,
        )
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
        store = (
            file_store
            if file_store is not None
            else LocalFileStore(resolve_file_store_path(resolved_settings))
        )
        application.state.rate_limiter = _rate_limiter(
            resolved_settings,
            session_factory,
            resolved_clock,
            rate_limiter,
        )
        application.state.audit_sink = _audit_sink(
            resolved_settings,
            session_factory,
            audit_sink,
        )
        application.state.file_store = store
        application.state.document_service = DocumentService(
            session_factory,
            store,
            max_upload_bytes=resolved_settings.max_upload_bytes,
            max_documents=resolved_settings.max_documents_per_tenant,
            max_storage_bytes=resolved_settings.max_storage_bytes_per_tenant,
            clock=resolved_clock,
            before_commit=before_document_commit,
            max_job_attempts=resolved_settings.worker_max_attempts,
        )
        if resolved_settings.warm_embeddings:
            loader = question_loader
            if loader is None:
                from app.questions.runtime import load_question_service

                loader = load_question_service
            application.state.question_service = await asyncio.to_thread(
                loader,
                resolved_settings,
                session_factory,
                application.state.qdrant_client,
            )
        try:
            yield
        finally:
            await application.state.qdrant_client.close()
            await engine.dispose()

    application = FastAPI(title="Engine RAG", version="0.1.0", lifespan=lifespan)
    application.state.settings = resolved_settings
    application.state.clock = resolved_clock

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

    @application.exception_handler(PayloadTooLargeError)
    async def handle_payload_too_large(
        _request: Request,
        exc: PayloadTooLargeError,
    ) -> JSONResponse:
        return payload_too_large_response(exc)

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request,
        _exc: RequestValidationError,
    ) -> JSONResponse:
        logging.getLogger("app.access").info("solicitud status=validation")
        return JSONResponse(
            status_code=422,
            content=error_payload("validation_error", "Los datos enviados no son válidos."),
        )

    @application.exception_handler(HTTPException)
    async def handle_http_exception(_request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else PUBLIC_FAILURE
        code = {
            400: "invalid_state",
            401: "authentication_failed",
            403: "authorization_failed",
            404: "not_found",
            409: "conflict",
            413: "file_too_large",
            429: "rate_limited",
        }.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(code, detail),
            headers=exc.headers,
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_error(_request: Request, _exc: Exception) -> JSONResponse:
        logging.getLogger("app.access").exception("solicitud status=error")
        return unexpected_error_response()

    @application.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        host = request.client.host if request.client is not None else None
        if not metrics_visible(host, resolved_settings) and not await _metrics_admin(request):
            return JSONResponse(
                status_code=404,
                content=error_payload("not_found", "No encontrado"),
            )
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @application.get("/healthz", summary="Comprueba que el proceso está vivo")
    def healthz() -> Response:
        return Response(content=_HEALTHZ_BODY, media_type="application/json")

    @application.get("/readyz", summary="Comprueba PostgreSQL, Qdrant y el modelo")
    async def readyz() -> JSONResponse:
        if postgres_check is not None and qdrant_check is not None:
            postgresql, qdrant = await asyncio.gather(postgres_check(), qdrant_check())
            ollama = await ollama_check() if ollama_check is not None else None
        else:
            postgresql, qdrant = await asyncio.gather(
                postgres_is_ready(application.state.session_factory),
                qdrant_is_ready(application.state.qdrant_client),
            )
            ollama = (
                await ollama_is_ready(resolved_settings.ollama_base_url)
                if provider_is_ollama
                else None
            )
        ready = postgresql and qdrant and (ollama is None or ollama)
        observe_dependency("postgresql", postgresql)
        observe_dependency("qdrant", qdrant)
        if ollama is not None:
            observe_dependency("ollama", ollama)
        return JSONResponse(
            status_code=200 if ready else 503,
            content=_ready_payload(postgresql, qdrant, ollama),
        )

    application.include_router(auth_router)
    application.include_router(api_keys_router)
    application.include_router(provider_tenants_router)
    application.include_router(users_router)
    application.include_router(documents_router)
    application.include_router(exports_router)
    application.include_router(questions_router)
    application.include_router(conversations_router)
    application.add_middleware(EdgeMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(resolved_settings),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Accept",
            "X-Request-ID",
            "X-CSRF-Token",
            "X-Export-Token",
        ],
    )
    return application


def _require_ready_checks(
    *,
    postgres_check: ComponentCheck | None,
    qdrant_check: ComponentCheck | None,
    ollama_check: ComponentCheck | None,
    provider_is_ollama: bool,
) -> None:
    if provider_is_ollama:
        provided = (
            postgres_check is not None,
            qdrant_check is not None,
            ollama_check is not None,
        )
        if any(provided) and not all(provided):
            message = "Las comprobaciones de readiness se sustituyen juntas"
            raise ValueError(message)
        return
    if (postgres_check is None) != (qdrant_check is None) or ollama_check is not None:
        message = "Las comprobaciones de readiness se sustituyen juntas"
        raise ValueError(message)


def _rate_limiter(
    settings: Settings,
    session_factory: object,
    clock: object,
    override: RateLimiter | None,
) -> RateLimiter:
    if override is not None:
        return override
    if not hasattr(clock, "now"):
        message = "El reloj del límite no es válido"
        raise RuntimeError(message)
    if settings.environment is Environment.TEST:
        return MemoryRateLimiter(cast(Clock, clock))
    from sqlalchemy.ext.asyncio import async_sessionmaker

    if not isinstance(session_factory, async_sessionmaker):
        message = "La fábrica de sesiones no es válida"
        raise RuntimeError(message)
    return PostgresRateLimiter(session_factory, cast(Clock, clock))


def _audit_sink(
    settings: Settings,
    session_factory: object,
    override: AuditSink | None,
) -> AuditSink:
    if override is not None:
        return override
    if settings.environment is Environment.TEST:
        return MemoryAuditSink()
    from sqlalchemy.ext.asyncio import async_sessionmaker

    if not isinstance(session_factory, async_sessionmaker):
        message = "La fábrica de sesiones no es válida"
        raise RuntimeError(message)
    return PostgresAuditSink(session_factory)


def _cors_origins(settings: Settings) -> list[str]:
    origins = list(cors_origin_list(settings))
    if origins:
        return origins
    return [DEFAULT_CORS_ORIGINS]


async def _metrics_admin(request: Request) -> bool:
    checker = getattr(request.app.state, "metrics_admin", None)
    if checker is not None:
        result = await checker(request)
        return bool(result)
    service = getattr(request.app.state, "auth_service", None)
    if not isinstance(service, AuthService):
        return False
    try:
        principal = await service.resolve_bearer(request.headers.get("authorization"))
    except AuthenticationError:
        return False
    return provider_admin(principal)


def _ready_payload(postgresql: bool, qdrant: bool, ollama: bool | None) -> dict[str, object]:
    components: dict[str, str] = {
        "postgresql": "ok" if postgresql else "unavailable",
        "qdrant": "ok" if qdrant else "unavailable",
    }
    ready = postgresql and qdrant
    if ollama is not None:
        components["ollama"] = "ok" if ollama else "unavailable"
        ready = ready and ollama
    return {
        "status": "ok" if ready else "unavailable",
        "components": components,
    }


app = create_app()
