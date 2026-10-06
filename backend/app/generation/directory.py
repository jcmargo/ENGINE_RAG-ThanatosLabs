"""Elige el modelo de cada pregunta. La clave externa no sale de este módulo."""

from __future__ import annotations

from collections.abc import Iterator
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

import httpx

from app.config import Settings
from app.domain.errors import InvalidStateError
from app.generation.base import GenerationRequest, GenerationResult, LLMProvider
from app.generation.errors import (
    LLMResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.generation.ollama import OllamaLLMProvider
from app.generation.openai_compatible import OpenAICompatibleProvider

EXTERNAL_LLM_ID = "external"
OLLAMA_PREFIX = "ollama:"
_UNKNOWN_MODEL = "El modelo no está disponible"
_EXTERNAL_MISSING = "La API externa no está configurada"
_TAGS_UNUSABLE = "Ollama devolvió una respuesta que no se puede usar"

ProviderName = Literal["ollama", "external"]

llm_choice: ContextVar[str | None] = ContextVar("engine_rag_llm_choice", default=None)
_resolved: ContextVar[tuple[str | None, LLMProvider] | None] = ContextVar(
    "engine_rag_llm_resolved",
    default=None,
)


@dataclass(frozen=True)
class ModelOption:
    """Modelo elegible. No incluye URL ni clave."""

    id: str
    label: str
    provider: ProviderName


@runtime_checkable
class LlmChoices(Protocol):
    """Catálogo que la API puede sustituir en pruebas."""

    def ensure(self, choice: str | None) -> None:
        """Rechaza un identificador que no está en la lista."""

    def list_models(self) -> tuple[ModelOption, ...]:
        """Modelos de Ollama y, si está configurada, la API externa."""


def bind_llm_choice(choice: str | None) -> Token[str | None]:
    """Fija el modelo de esta pregunta. Hay que restaurar el token al terminar."""
    _resolved.set(None)
    cleaned = None if choice is None or not choice.strip() else choice.strip()
    return llm_choice.set(cleaned)


def reset_llm_choice(token: Token[str | None]) -> None:
    """Restaura la elección anterior y olvida el proveedor ya resuelto."""
    llm_choice.reset(token)
    _resolved.set(None)


class ConfiguredLlmDirectory:
    """Lee los modelos de Ollama y construye el proveedor de la pregunta."""

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client
        self._base_url = settings.ollama_base_url.rstrip("/")
        self._default_model = settings.llm_model.strip()
        connect = settings.llm_connect_timeout_seconds
        read = settings.llm_timeout_seconds
        self._http_timeout = httpx.Timeout(connect=connect, read=read, write=read, pool=connect)

    def list_models(self) -> tuple[ModelOption, ...]:
        options = [
            ModelOption(id=f"{OLLAMA_PREFIX}{name}", label=name, provider="ollama")
            for name in self._ollama_names()
        ]
        external = self._external_option()
        if external is not None:
            options.append(external)
        return tuple(options)

    def ensure(self, choice: str | None) -> None:
        self.resolve(choice)

    def resolve(self, choice: str | None) -> LLMProvider:
        selected = self._selected(choice)
        if selected == EXTERNAL_LLM_ID:
            return self._external_provider()
        if not selected.startswith(OLLAMA_PREFIX):
            raise InvalidStateError(_UNKNOWN_MODEL)
        name = selected.removeprefix(OLLAMA_PREFIX).strip()
        if not name or name not in self._ollama_names():
            raise InvalidStateError(_UNKNOWN_MODEL)
        return OllamaLLMProvider(
            base_url=self._settings.ollama_base_url,
            model=name,
            temperature=self._settings.llm_temperature,
            connect_timeout_seconds=self._settings.llm_connect_timeout_seconds,
            timeout_seconds=self._settings.llm_timeout_seconds,
            client=self._client,
        )

    def _selected(self, choice: str | None) -> str:
        if choice is None or not choice.strip():
            return f"{OLLAMA_PREFIX}{self._default_model}"
        return choice.strip()

    def _external_option(self) -> ModelOption | None:
        if not _external_is_configured(self._settings):
            return None
        return ModelOption(
            id=EXTERNAL_LLM_ID,
            label=self._settings.external_llm_model.strip(),
            provider="external",
        )

    def _external_provider(self) -> OpenAICompatibleProvider:
        if not _external_is_configured(self._settings):
            raise InvalidStateError(_EXTERNAL_MISSING)
        return OpenAICompatibleProvider(
            base_url=self._settings.external_llm_base_url.strip(),
            api_key=self._settings.external_llm_api_key.get_secret_value(),
            model=self._settings.external_llm_model.strip(),
            temperature=self._settings.llm_temperature,
            connect_timeout_seconds=self._settings.llm_connect_timeout_seconds,
            timeout_seconds=self._settings.llm_timeout_seconds,
            client=self._client,
        )

    def _ollama_names(self) -> tuple[str, ...]:
        client = self._client
        owns_client = client is None
        if client is None:
            client = httpx.Client(timeout=self._http_timeout)
        try:
            return _read_tags(client, self._base_url)
        finally:
            if owns_client:
                client.close()


class RoutingLLMProvider:
    """Proveedor de la pregunta en curso. La elección viaja en un contexto de tarea."""

    def __init__(self, directory: ConfiguredLlmDirectory) -> None:
        self._directory = directory

    @property
    def model_version(self) -> str:
        return self._provider().model_version

    def generate(self, request: GenerationRequest) -> GenerationResult:
        return self._provider().generate(request)

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        return self._provider().stream(request)

    def _provider(self) -> LLMProvider:
        choice = llm_choice.get()
        cached = _resolved.get()
        if cached is not None and cached[0] == choice:
            return cached[1]
        provider = self._directory.resolve(choice)
        _resolved.set((choice, provider))
        return provider


def _external_is_configured(settings: Settings) -> bool:
    key = settings.external_llm_api_key.get_secret_value().strip()
    return bool(
        settings.external_llm_base_url.strip() and key and settings.external_llm_model.strip()
    )


def _read_tags(client: httpx.Client, base_url: str) -> tuple[str, ...]:
    try:
        response = client.get(f"{base_url}/api/tags")
    except httpx.TimeoutException as error:
        message = "Ollama no respondió a tiempo"
        raise LLMTimeoutError(message) from error
    except httpx.HTTPError as error:
        message = "Ollama no está disponible"
        raise LLMUnavailableError(message) from error
    if response.status_code != 200:
        raise LLMUnavailableError(_UNKNOWN_MODEL)
    try:
        payload: object = response.json()
    except ValueError as error:
        raise LLMResponseError(_TAGS_UNUSABLE) from error
    return _tag_names(payload)


def _tag_names(payload: object) -> tuple[str, ...]:
    if not isinstance(payload, dict):
        raise LLMResponseError(_TAGS_UNUSABLE)
    models = payload.get("models")
    if not isinstance(models, list):
        raise LLMResponseError(_TAGS_UNUSABLE)
    names: dict[str, None] = {}
    for item in models:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and name.strip():
            names.setdefault(name.strip(), None)
    return tuple(sorted(names, key=str.casefold))
