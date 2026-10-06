"""Adaptador HTTP de Ollama. No registra el prompt ni la respuesta."""

import json
import logging
import time
from collections.abc import Iterator

import httpx

from app.generation.base import (
    GenerationRequest,
    GenerationResult,
    streaming_not_available,
)
from app.generation.errors import (
    GenerationError,
    LLMResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
)

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_LLM_MODEL = "qwen2.5:7b"


class OllamaLLMProvider:
    """Cliente de `POST /api/chat` con timeouts de conexión y de lectura."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_OLLAMA_BASE_URL,
        model: str = DEFAULT_LLM_MODEL,
        temperature: float = 0.0,
        connect_timeout_seconds: float = 5.0,
        timeout_seconds: float = 120.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not base_url.strip():
            message = "La URL de Ollama no puede estar vacía"
            raise ValueError(message)
        if not model.strip():
            message = "El modelo de Ollama no puede estar vacío"
            raise ValueError(message)
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._temperature = _non_negative("temperature", temperature)
        self._timeout_seconds = _positive("timeout_seconds", timeout_seconds)
        connect_timeout = _positive("connect_timeout_seconds", connect_timeout_seconds)
        self._http_timeout = httpx.Timeout(
            connect=connect_timeout,
            read=self._timeout_seconds,
            write=self._timeout_seconds,
            pool=connect_timeout,
        )
        self._client = client

    @property
    def model_version(self) -> str:
        return self._model

    def generate(self, request: GenerationRequest) -> GenerationResult:
        started = time.perf_counter()
        try:
            content = self._post(request)
        except GenerationError:
            self._log(started, "error")
            raise
        self._log(started, "ok")
        return GenerationResult(text=content)

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        return streaming_not_available(request)

    def _post(self, request: GenerationRequest) -> str:
        client = self._client
        owns_client = client is None
        if client is None:
            client = httpx.Client(timeout=self._http_timeout)
        try:
            response = self._send(client, request)
        finally:
            if owns_client:
                client.close()
        if response.status_code != 200:
            message = (
                f"Ollama respondió HTTP {response.status_code} para el modelo {self._model}. "
                f"Comprueba que el modelo está descargado con: ollama pull {self._model}"
            )
            raise LLMResponseError(message)
        try:
            payload: object = response.json()
        except json.JSONDecodeError as error:
            message = "Ollama devolvió una respuesta que no se puede usar"
            raise LLMResponseError(message) from error
        return _message_content(payload)

    def _send(self, client: httpx.Client, request: GenerationRequest) -> httpx.Response:
        try:
            return client.post(f"{self._base_url}/api/chat", json=self._payload(request))
        except httpx.ConnectTimeout as error:
            message = (
                f"Ollama no está disponible en {self._base_url}. "
                f"Inicia Ollama y comprueba que el modelo {self._model} está descargado."
            )
            raise LLMUnavailableError(message) from error
        except httpx.TimeoutException as error:
            message = (
                f"Ollama no respondió en {self._timeout_seconds:g} segundos "
                f"en {self._base_url}. Inicia Ollama o aumenta RAG_LLM_TIMEOUT_SECONDS."
            )
            raise LLMTimeoutError(message) from error
        except httpx.HTTPError as error:
            message = (
                f"Ollama no está disponible en {self._base_url}. "
                f"Inicia Ollama y comprueba que el modelo {self._model} está descargado."
            )
            raise LLMUnavailableError(message) from error

    def _payload(self, request: GenerationRequest) -> dict[str, object]:
        return {
            "model": self._model,
            "stream": False,
            "think": False,
            "format": "json",
            "keep_alive": "30m",
            "options": {"temperature": self._temperature, "num_predict": 512},
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
        }

    def _log(self, started: float, status: str) -> None:
        logger.info(
            "llm provider=ollama model=%s duration_ms=%.3f status=%s",
            self._model,
            (time.perf_counter() - started) * 1000,
            status,
        )


def _message_content(payload: object) -> str:
    if not isinstance(payload, dict):
        message = "Ollama devolvió una respuesta que no se puede usar"
        raise LLMResponseError(message)
    message_obj = payload.get("message")
    if not isinstance(message_obj, dict):
        message = "Ollama devolvió una respuesta que no se puede usar"
        raise LLMResponseError(message)
    content = message_obj.get("content")
    if not isinstance(content, str) or not content.strip():
        message = "Ollama devolvió una respuesta vacía"
        raise LLMResponseError(message)
    return content


def _positive(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        message = f"{name} debe ser un número mayor que cero"
        raise ValueError(message)
    return float(value)


def _non_negative(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        message = f"{name} debe ser un número mayor o igual que cero"
        raise ValueError(message)
    return float(value)
