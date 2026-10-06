"""Configuración tipada del proceso, leída de variables `RAG_` y de `.env` opcional."""

from enum import StrEnum
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.errors import InvalidStateError
from app.domain.evidence_policy import DEFAULT_LEXICAL_FLOOR
from app.embeddings.base import DEFAULT_EMBEDDING_MODEL
from app.generation.ollama import DEFAULT_LLM_MODEL, DEFAULT_OLLAMA_BASE_URL
from app.retrieval.contracts import DEFAULT_MAX_QUESTION_CHARS
from app.retrieval.vectors import DEFAULT_VECTOR_ALIAS, require_alias_name

# Solo para desarrollo y test locales. Producción debe fallar si se usa.
DEFAULT_JWT_SECRET = "local-dev-only-jwt-secret-min-32b!"
DEFAULT_PSEUDONYM_SECRET = "local-dev-only-pseudonym-secret-32b"
DEFAULT_POSTGRES_PASSWORD = "engine_rag_local"
DEFAULT_CORS_ORIGINS = "http://localhost:5173"
DEFAULT_JWT_ISSUER = "engine-rag"
DEFAULT_JWT_AUDIENCE = "engine-rag-api"
DEFAULT_ACCESS_TOKEN_MINUTES = 15
DEFAULT_REFRESH_TOKEN_DAYS = 7
DEFAULT_REFRESH_COOKIE_NAME = "rag_refresh"
DEFAULT_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_DOCUMENTS_PER_TENANT = 500
DEFAULT_MAX_STORAGE_BYTES_PER_TENANT = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_CONTEXT_CHARS = 12_000


def _repository_root() -> Path:
    """Localiza la raíz. La imagen usa `ENGINE_RAG_ROOT` y no lleva `.env`."""
    start = Path(__file__).resolve().parent
    for candidate in (start, *start.parents):
        backend_project = candidate / "backend" / "pyproject.toml"
        if not backend_project.is_file():
            continue
        if (candidate / ".env.example").is_file() or (candidate / "ENGINE_RAG_ROOT").is_file():
            return candidate
    message = "No se encontró la raíz del repositorio a partir de la configuración"
    raise RuntimeError(message)


def repository_root() -> Path:
    """Raíz del repositorio, detectada desde este módulo."""
    return _repository_root()


_ENV_FILE = _repository_root() / ".env"


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LLMProviderName(StrEnum):
    """Proveedor de lenguaje habilitado. Un valor distinto no arranca."""

    OLLAMA = "ollama"


class Settings(BaseSettings):
    """Valores de ejecución. Los defaults son seguros para desarrollo local."""

    model_config = SettingsConfigDict(
        env_prefix="RAG_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    environment: Environment = Environment.DEVELOPMENT
    log_level: LogLevel = LogLevel.INFO
    api_host: str = Field(default="127.0.0.1", min_length=1)
    api_port: int = Field(default=8000, ge=1, le=65535)
    embedding_model: str = Field(default=DEFAULT_EMBEDDING_MODEL, min_length=1)
    # `python -m app` lo activa en el proceso de uvicorn. La primera pregunta no carga el modelo.
    warm_embeddings: bool = False
    model_cache_dir: Path | None = None
    llm_model: str = Field(default=DEFAULT_LLM_MODEL, min_length=1)
    llm_provider: LLMProviderName = LLMProviderName.OLLAMA
    ollama_base_url: str = Field(default=DEFAULT_OLLAMA_BASE_URL, min_length=1)
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_connect_timeout_seconds: float = Field(default=5.0, gt=0.0)
    llm_timeout_seconds: float = Field(default=120.0, gt=0.0)
    # Si falta cualquiera, el selector no ofrece la API externa. La clave no se registra.
    external_llm_base_url: str = ""
    external_llm_api_key: SecretStr = SecretStr("")
    external_llm_model: str = ""
    # 0.857 es el corte más alto que aún acepta soporte (0.857024),
    # vacaciones (0.866878) y gastos (0.910368). El mayor distractor
    # medido sobre data/sample fue 0.851205.
    min_retrieval_score: float = Field(default=0.857, ge=0.0, le=1.0)
    # Entre este piso y el semántico solo entra un fragmento que cubre al menos
    # la mitad de las palabras de la pregunta. 0.80 cubre las paráfrasis medidas.
    min_lexical_score: float = Field(default=DEFAULT_LEXICAL_FLOOR, ge=0.0, le=1.0)
    max_question_chars: int = Field(default=DEFAULT_MAX_QUESTION_CHARS, ge=1)
    max_context_chars: int = Field(default=DEFAULT_MAX_CONTEXT_CHARS, ge=1)
    conversation_retention_days: int = Field(default=30, ge=1)
    trace_retention_days: int = Field(default=90, ge=1)
    feedback_reason_max_chars: int = Field(default=500, ge=1, le=500)
    postgres_user: str = Field(default="engine_rag", min_length=1)
    # Solo para desarrollo local. Compose y RAG_DATABASE_URL deben usar la misma.
    postgres_password: str = Field(default="engine_rag_local", min_length=1)
    postgres_db: str = Field(default="engine_rag", min_length=1)
    # Independiente de RAG_POSTGRES_PASSWORD: mantén el mismo password en ambos.
    database_url: str = Field(
        default="postgresql+asyncpg://engine_rag:engine_rag_local@127.0.0.1:5432/engine_rag",
        min_length=1,
    )
    qdrant_url: str = Field(default="http://127.0.0.1:6333", min_length=1)
    jwt_secret: str = Field(default=DEFAULT_JWT_SECRET, min_length=1)
    jwt_issuer: str = Field(default=DEFAULT_JWT_ISSUER, min_length=1)
    jwt_audience: str = Field(default=DEFAULT_JWT_AUDIENCE, min_length=1)
    access_token_minutes: int = Field(default=DEFAULT_ACCESS_TOKEN_MINUTES, ge=1)
    refresh_token_days: int = Field(default=DEFAULT_REFRESH_TOKEN_DAYS, ge=1)
    refresh_cookie_name: str = Field(default=DEFAULT_REFRESH_COOKIE_NAME, min_length=1)
    file_store_path: Path = Field(default=Path("data/uploads"))
    max_upload_bytes: int = Field(default=DEFAULT_MAX_UPLOAD_BYTES, ge=1)
    max_documents_per_tenant: int = Field(default=DEFAULT_MAX_DOCUMENTS_PER_TENANT, ge=1)
    max_storage_bytes_per_tenant: int = Field(
        default=DEFAULT_MAX_STORAGE_BYTES_PER_TENANT,
        ge=1,
    )
    worker_poll_seconds: float = Field(default=1.0, gt=0)
    worker_lease_seconds: int = Field(default=60, ge=1)
    worker_heartbeat_seconds: int = Field(default=15, ge=1)
    worker_max_attempts: int = Field(default=5, ge=1)
    worker_backoff_base_seconds: float = Field(default=2.0, gt=0)
    worker_backoff_cap_seconds: float = Field(default=300.0, gt=0)
    embed_batch_size: int = Field(default=16, ge=1)
    index_revision: str = Field(default="v1", min_length=1)
    vector_alias: str = Field(default=DEFAULT_VECTOR_ALIAS, min_length=1)
    pseudonym_secret: str = Field(default=DEFAULT_PSEUDONYM_SECRET, min_length=1)
    cors_origins: str = Field(default=DEFAULT_CORS_ORIGINS)
    metrics_allow_cidrs: str = Field(default="127.0.0.1/32,::1/128")
    public_https: bool = False
    refresh_cookie_secure: bool = False
    csrf_cookie_name: str = Field(default="rag_csrf", min_length=1)
    audit_retention_days: int = Field(default=90, ge=1)
    restic_repository: str = Field(  # type: ignore[pydantic-alias]
        default="",
        validation_alias=AliasChoices("RESTIC_REPOSITORY", "RAG_RESTIC_REPOSITORY"),
    )
    restic_password_file: Path | None = Field(  # type: ignore[pydantic-alias]
        default=None,
        validation_alias=AliasChoices("RESTIC_PASSWORD_FILE", "RAG_RESTIC_PASSWORD_FILE"),
    )
    restic_prune_password_file: Path | None = Field(  # type: ignore[pydantic-alias]
        default=None,
        validation_alias=AliasChoices(
            "RESTIC_PRUNE_PASSWORD_FILE",
            "RAG_RESTIC_PRUNE_PASSWORD_FILE",
        ),
    )
    backup_lock_seconds: int = Field(default=3600, ge=60)
    export_ttl_hours: int = Field(default=24, ge=1)
    retention_batch_size: int = Field(default=100, ge=1, le=1000)
    retention_interval_hours: int = Field(default=24, ge=1)
    rate_login_limit: int = Field(default=30, ge=1)
    rate_login_window_seconds: int = Field(default=60, ge=1)
    rate_refresh_limit: int = Field(default=60, ge=1)
    rate_refresh_window_seconds: int = Field(default=60, ge=1)
    rate_questions_limit: int = Field(default=60, ge=1)
    rate_questions_window_seconds: int = Field(default=60, ge=1)
    rate_uploads_limit: int = Field(default=30, ge=1)
    rate_uploads_window_seconds: int = Field(default=60, ge=1)
    rate_admin_limit: int = Field(default=120, ge=1)
    rate_admin_window_seconds: int = Field(default=60, ge=1)

    @field_validator("restic_password_file", "restic_prune_password_file", mode="before")
    @classmethod
    def blank_password_file_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("model_cache_dir", mode="before")
    @classmethod
    def blank_model_cache_dir_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("environment", mode="before")
    @classmethod
    def normalize_environment(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("llm_provider", mode="before")
    @classmethod
    def normalize_llm_provider(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @model_validator(mode="after")
    def worker_timing_is_consistent(self) -> "Settings":
        if self.worker_lease_seconds <= self.worker_heartbeat_seconds:
            message = "RAG_WORKER_LEASE_SECONDS debe ser mayor que el heartbeat"
            raise ValueError(message)
        if self.worker_backoff_cap_seconds < self.worker_backoff_base_seconds:
            message = "El tope del backoff debe cubrir la base"
            raise ValueError(message)
        if "|" in self.index_revision:
            message = "RAG_INDEX_REVISION no puede contener '|'"
            raise ValueError(message)
        try:
            require_alias_name(self.vector_alias)
        except InvalidStateError as error:
            raise ValueError(error.message) from error
        return self


def get_settings() -> Settings:
    """Carga la configuración del entorno actual."""
    return Settings()


def resolve_model_cache_dir(settings: Settings) -> Path | None:
    """Resuelve `RAG_MODEL_CACHE_DIR` contra la raíz cuando la ruta es relativa."""
    cache_dir = settings.model_cache_dir
    if cache_dir is None:
        return None
    if cache_dir.is_absolute():
        return cache_dir
    return repository_root() / cache_dir


def resolve_file_store_path(settings: Settings) -> Path:
    """Resuelve `RAG_FILE_STORE_PATH` contra la raíz cuando la ruta es relativa."""
    path = settings.file_store_path
    if path.is_absolute():
        return path
    return repository_root() / path


def cors_origin_list(settings: Settings) -> tuple[str, ...]:
    """Allowlist explícita. Un hueco no cuenta como origen."""
    return tuple(part.strip() for part in settings.cors_origins.split(",") if part.strip())


def validate_jwt_settings_for_environment(settings: Settings) -> None:
    """Falla en producción si un secreto, el origen o la cookie no son seguros."""
    if settings.environment is not Environment.PRODUCTION:
        return
    secret = settings.jwt_secret
    if secret == DEFAULT_JWT_SECRET:
        message = "RAG_JWT_SECRET no puede ser el valor por defecto en producción"
        raise RuntimeError(message)
    if len(secret.encode("utf-8")) < 32:
        message = "RAG_JWT_SECRET debe tener al menos 32 bytes en producción"
        raise RuntimeError(message)
    if (
        settings.postgres_password == DEFAULT_POSTGRES_PASSWORD
        or DEFAULT_POSTGRES_PASSWORD in settings.database_url
    ):
        message = "RAG_POSTGRES_PASSWORD no puede ser el valor por defecto en producción"
        raise RuntimeError(message)
    pseudonym = settings.pseudonym_secret
    if pseudonym == DEFAULT_PSEUDONYM_SECRET or len(pseudonym.encode("utf-8")) < 32:
        message = "RAG_PSEUDONYM_SECRET debe ser propio y de al menos 32 bytes en producción"
        raise RuntimeError(message)
    origins = cors_origin_list(settings)
    if not origins or any(origin == "*" or "*" in origin for origin in origins):
        message = "RAG_CORS_ORIGINS no puede estar vacío ni usar comodín en producción"
        raise RuntimeError(message)
    if not settings.refresh_cookie_secure:
        message = "La cookie de refresh debe ser Secure en producción"
        raise RuntimeError(message)
