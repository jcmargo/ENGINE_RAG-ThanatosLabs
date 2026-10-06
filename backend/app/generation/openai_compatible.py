"""Adaptador de una API de chat compatible con OpenAI. No registra la clave."""

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

_UNUSABLE = "La API externa devolvió una respuesta que no se puede usar"


class OpenAICompatibleProvider:
    """Cliente de `POST {base}/chat/completions` con temperatura fija en la llamada."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.0,
        connect_timeout_seconds: float = 5.0,
        timeout_seconds: float = 120.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not base_url.strip():
            message = "La URL de la API externa no puede estar vacía"
            raise ValueError(message)
        if not api_key.strip():
            message = "La clave de la API externa no puede estar vacía"
            raise ValueError(message)
        if not model.strip():
            message = "El modelo de la API externa no puede estar vacío"
            raise ValueError(message)
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model.strip()
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
            message = "La API externa no pudo completar la respuesta"
            raise LLMResponseError(message)
        try:
            payload: object = response.json()
        except json.JSONDecodeError as error:
            raise LLMResponseError(_UNUSABLE) from error
        return _message_content(payload)

    def _send(self, client: httpx.Client, request: GenerationRequest) -> httpx.Response:
        try:
            return client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=self._payload(request),
            )
        except httpx.TimeoutException as error:
            message = "La API externa no respondió a tiempo"
            raise LLMTimeoutError(message) from error
        except httpx.HTTPError as error:
            message = "La API externa no está disponible"
            raise LLMUnavailableError(message) from error

    def _payload(self, request: GenerationRequest) -> dict[str, object]:
        return {
            "model": self._model,
            "temperature": self._temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
        }

    def _log(self, started: float, status: str) -> None:
        logger.info(
            "llm provider=external model=%s duration_ms=%.3f status=%s",
            self._model,
            (time.perf_counter() - started) * 1000,
            status,
        )


def _message_content(payload: object) -> str:
    if not isinstance(payload, dict):
        raise LLMResponseError(_UNUSABLE)
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) == 0:
        raise LLMResponseError(_UNUSABLE)
    first = choices[0]
    if not isinstance(first, dict):
        raise LLMResponseError(_UNUSABLE)
    message_obj = first.get("message")
    if not isinstance(message_obj, dict):
        raise LLMResponseError(_UNUSABLE)
    content = message_obj.get("content")
    if not isinstance(content, str) or not content.strip():
        message = "La API externa devolvió una respuesta vacía"
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
