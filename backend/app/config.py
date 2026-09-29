"""Configuración tipada del proceso, leída de variables `RAG_` y de `.env` opcional."""

from enum import StrEnum
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.embeddings.base import DEFAULT_EMBEDDING_MODEL
from app.generation.ollama import DEFAULT_LLM_MODEL, DEFAULT_OLLAMA_BASE_URL


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
