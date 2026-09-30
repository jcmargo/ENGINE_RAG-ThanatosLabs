"""Configuración por entorno, con valores seguros de desarrollo."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from app.config import (
    DEFAULT_JWT_SECRET,
    Environment,
    LogLevel,
    Settings,
    get_settings,
    repository_root,
    resolve_model_cache_dir,
)


@pytest.fixture(autouse=True)
def _clear_rag_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "RAG_ENVIRONMENT",
        "RAG_LOG_LEVEL",
        "RAG_API_HOST",
        "RAG_API_PORT",
        "RAG_EMBEDDING_MODEL",
        "RAG_MODEL_CACHE_DIR",
        "RAG_LLM_MODEL",
        "RAG_OLLAMA_BASE_URL",
        "RAG_LLM_TEMPERATURE",
        "RAG_LLM_CONNECT_TIMEOUT_SECONDS",
        "RAG_LLM_TIMEOUT_SECONDS",
        "RAG_MIN_RETRIEVAL_SCORE",
        "RAG_POSTGRES_USER",
        "RAG_POSTGRES_PASSWORD",
        "RAG_POSTGRES_DB",
        "RAG_DATABASE_URL",
        "RAG_QDRANT_URL",
        "RAG_JWT_SECRET",
        "RAG_JWT_ISSUER",
        "RAG_JWT_AUDIENCE",
        "RAG_ACCESS_TOKEN_MINUTES",
        "RAG_REFRESH_TOKEN_DAYS",
        "RAG_REFRESH_COOKIE_NAME",
    ):
        monkeypatch.delenv(name, raising=False)


def _isolated() -> type[Settings]:
    class IsolatedSettings(Settings):
        model_config = SettingsConfigDict(
            env_prefix="RAG_",
            env_file=None,
            extra="ignore",
            frozen=True,
        )

    return IsolatedSettings


def test_defaults_are_safe_for_development() -> None:
    settings = _isolated()()

    assert settings.environment is Environment.DEVELOPMENT
    assert settings.log_level is LogLevel.INFO
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("development", Environment.DEVELOPMENT),
        ("test", Environment.TEST),
        ("production", Environment.PRODUCTION),
        ("Production", Environment.PRODUCTION),
    ],
)
def test_accepts_named_environments(raw: str, expected: Environment) -> None:
    settings = _isolated().model_validate({"environment": raw})

    assert settings.environment is expected


def test_rejects_unknown_environment() -> None:
    with pytest.raises(ValidationError):
        _isolated().model_validate({"environment": "staging"})


@pytest.mark.parametrize("port", [1, 65535])
def test_accepts_port_bounds(port: int) -> None:
    settings = _isolated().model_validate({"api_port": port})

    assert settings.api_port == port


@pytest.mark.parametrize("port", [0, 65536])
def test_rejects_port_outside_tcp_range(port: int) -> None:
    with pytest.raises(ValidationError):
        _isolated().model_validate({"api_port": port})


def test_rejects_empty_host() -> None:
    with pytest.raises(ValidationError):
        _isolated().model_validate({"api_host": ""})


def test_normalizes_log_level() -> None:
    settings = _isolated().model_validate({"log_level": "debug"})

    assert settings.log_level is LogLevel.DEBUG


def test_embedding_settings_default_to_local_e5() -> None:
    settings = _isolated()()

    assert settings.embedding_model == "intfloat/multilingual-e5-base"
    assert settings.model_cache_dir is None
    assert resolve_model_cache_dir(settings) is None
    assert settings.llm_model == "qwen2.5:7b"
    assert settings.ollama_base_url == "http://127.0.0.1:11434"
    assert settings.llm_temperature == 0.0
    assert settings.llm_connect_timeout_seconds == 5.0
    assert settings.llm_timeout_seconds == 120.0
    assert settings.min_retrieval_score == 0.8
    assert settings.postgres_user == "engine_rag"
    assert settings.postgres_password == "engine_rag_local"
    assert settings.postgres_db == "engine_rag"
    assert (
        settings.database_url
        == "postgresql+asyncpg://engine_rag:engine_rag_local@127.0.0.1:5432/engine_rag"
    )
    assert settings.qdrant_url == "http://127.0.0.1:6333"
    assert settings.jwt_secret == DEFAULT_JWT_SECRET
    assert settings.jwt_issuer == "engine-rag"
    assert settings.jwt_audience == "engine-rag-api"
    assert settings.access_token_minutes == 15
    assert settings.refresh_token_days == 7
    assert settings.refresh_cookie_name == "rag_refresh"


def test_blank_model_cache_dir_is_none() -> None:
    settings = _isolated().model_validate({"model_cache_dir": "  "})

    assert settings.model_cache_dir is None


def test_relative_model_cache_dir_resolves_from_repository_root() -> None:
    settings = _isolated().model_validate({"model_cache_dir": "models/embeddings"})

    assert resolve_model_cache_dir(settings) == repository_root() / "models" / "embeddings"


def test_absolute_model_cache_dir_stays_absolute(tmp_path: Path) -> None:
    settings = _isolated().model_validate({"model_cache_dir": tmp_path})

    assert resolve_model_cache_dir(settings) == tmp_path


def test_get_settings_reads_llm_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_LLM_MODEL", "qwen2.5:3b")
    monkeypatch.setenv("RAG_OLLAMA_BASE_URL", "http://127.0.0.1:11435")
    monkeypatch.setenv("RAG_LLM_TEMPERATURE", "0.1")
    monkeypatch.setenv("RAG_LLM_CONNECT_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("RAG_LLM_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("RAG_MIN_RETRIEVAL_SCORE", "0.8")

    settings = get_settings()

    assert settings.llm_model == "qwen2.5:3b"
    assert settings.ollama_base_url == "http://127.0.0.1:11435"
    assert settings.llm_temperature == 0.1
    assert settings.llm_connect_timeout_seconds == 2
    assert settings.llm_timeout_seconds == 30
    assert settings.min_retrieval_score == 0.8


def test_get_settings_reads_infrastructure_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_POSTGRES_USER", "rag_user")
    monkeypatch.setenv("RAG_POSTGRES_PASSWORD", "rag_local")
    monkeypatch.setenv("RAG_POSTGRES_DB", "rag_db")
    monkeypatch.setenv(
        "RAG_DATABASE_URL",
        "postgresql+asyncpg://rag_user:rag_local@127.0.0.1:5432/rag_db",
    )
    monkeypatch.setenv("RAG_QDRANT_URL", "http://127.0.0.1:6333")

    settings = get_settings()

    assert settings.postgres_user == "rag_user"
    assert settings.postgres_password == "rag_local"
    assert settings.postgres_db == "rag_db"
    assert settings.database_url == "postgresql+asyncpg://rag_user:rag_local@127.0.0.1:5432/rag_db"
    assert settings.qdrant_url == "http://127.0.0.1:6333"


def test_rejects_empty_database_url() -> None:
    with pytest.raises(ValidationError):
        _isolated().model_validate({"database_url": ""})


def test_rejects_empty_qdrant_url() -> None:
    with pytest.raises(ValidationError):
        _isolated().model_validate({"qdrant_url": ""})


def test_rejects_retrieval_score_outside_unit_interval() -> None:
    with pytest.raises(ValidationError):
        _isolated().model_validate({"min_retrieval_score": 1.1})


def test_rejects_empty_llm_model() -> None:
    with pytest.raises(ValidationError):
        _isolated().model_validate({"llm_model": ""})


def test_rejects_empty_embedding_model() -> None:
    with pytest.raises(ValidationError):
        _isolated().model_validate({"embedding_model": ""})


def test_get_settings_reads_embedding_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "intfloat/multilingual-e5-base")
    monkeypatch.setenv("RAG_MODEL_CACHE_DIR", "models/embeddings")

    settings = get_settings()

    assert settings.embedding_model == "intfloat/multilingual-e5-base"
    assert resolve_model_cache_dir(settings) == repository_root() / "models" / "embeddings"


def test_get_settings_reads_rag_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_ENVIRONMENT", "production")
    monkeypatch.setenv("RAG_LOG_LEVEL", "error")
    monkeypatch.setenv("RAG_API_HOST", "localhost")
    monkeypatch.setenv("RAG_API_PORT", "9001")

    settings = get_settings()

    assert settings.environment is Environment.PRODUCTION
    assert settings.log_level is LogLevel.ERROR
    assert settings.api_host == "localhost"
    assert settings.api_port == 9001


def test_missing_env_file_is_optional(tmp_path: Path) -> None:
    missing = tmp_path / ".env"

    class MissingFileSettings(Settings):
        model_config = SettingsConfigDict(
            env_prefix="RAG_",
            env_file=missing,
            env_file_encoding="utf-8",
            extra="ignore",
            frozen=True,
        )

    settings = MissingFileSettings()

    assert not missing.is_file()
    assert settings.environment is Environment.DEVELOPMENT
