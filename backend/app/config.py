"""Configuración tipada del proceso, leída de variables `RAG_` y de `.env` opcional."""

from enum import StrEnum
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.embeddings.base import DEFAULT_EMBEDDING_MODEL
from app.generation.ollama import DEFAULT_LLM_MODEL, DEFAULT_OLLAMA_BASE_URL

# Solo para desarrollo y test locales. Producción debe fallar si se usa.
DEFAULT_JWT_SECRET = "local-dev-only-jwt-secret-min-32b!"
DEFAULT_JWT_ISSUER = "engine-rag"
DEFAULT_JWT_AUDIENCE = "engine-rag-api"
DEFAULT_ACCESS_TOKEN_MINUTES = 15
DEFAULT_REFRESH_TOKEN_DAYS = 7
DEFAULT_REFRESH_COOKIE_NAME = "rag_refresh"


def _repository_root() -> Path:
    start = Path(__file__).resolve().parent
    for candidate in (start, *start.parents):
        env_example = candidate / ".env.example"
        backend_project = candidate / "backend" / "pyproject.toml"
        if env_example.is_file() and backend_project.is_file():
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
    model_cache_dir: Path | None = None
    llm_model: str = Field(default=DEFAULT_LLM_MODEL, min_length=1)
    ollama_base_url: str = Field(default=DEFAULT_OLLAMA_BASE_URL, min_length=1)
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_connect_timeout_seconds: float = Field(default=5.0, gt=0.0)
    llm_timeout_seconds: float = Field(default=120.0, gt=0.0)
    # 0.8 separa las tres preguntas de data/sample (mínimo observado 0.857)
    # de preguntas fuera de dominio (máximo observado 0.753).
    min_retrieval_score: float = Field(default=0.8, ge=0.0, le=1.0)
    postgres_user: str = Field(default="engine_rag", min_length=1)
    postgres_password: str = Field(default="engine_rag_local", min_length=1)
    postgres_db: str = Field(default="engine_rag", min_length=1)
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


def validate_jwt_settings_for_environment(settings: Settings) -> None:
    """Falla en producción si el secreto JWT es el default local o es corto."""
    if settings.environment is not Environment.PRODUCTION:
        return
    secret = settings.jwt_secret
    if secret == DEFAULT_JWT_SECRET:
        message = "RAG_JWT_SECRET no puede ser el valor por defecto en producción"
        raise RuntimeError(message)
    if len(secret.encode("utf-8")) < 32:
        message = "RAG_JWT_SECRET debe tener al menos 32 bytes en producción"
        raise RuntimeError(message)
