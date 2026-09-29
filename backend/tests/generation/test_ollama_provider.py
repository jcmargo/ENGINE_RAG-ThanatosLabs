"""Adaptador de Ollama con transporte falso. No abre un puerto."""

import json
import logging

import httpx
import pytest

from app.generation.base import GenerationRequest
from app.generation.errors import (
    LLMResponseError,
    LLMStreamingNotAvailableError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.generation.ollama import OllamaLLMProvider

_REQUEST = GenerationRequest(system_prompt="sistema", user_prompt="PREGUNTA-SECRETA-03")


def _provider(transport: httpx.MockTransport) -> OllamaLLMProvider:
    return OllamaLLMProvider(
        base_url="http://127.0.0.1:11434",
        model="qwen2.5:7b",
        temperature=0.0,
        client=httpx.Client(transport=transport),
    )


def test_posts_structured_chat_and_returns_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "RESPUESTA-SECRETA-03"}},
        )

    provider = _provider(httpx.MockTransport(handler))
    with caplog.at_level(logging.DEBUG):
        result = provider.generate(_REQUEST)

    body = seen["body"]
    assert isinstance(body, dict)
    assert seen["path"] == "/api/chat"
    assert body["model"] == "qwen2.5:7b"
    assert body["stream"] is False
    assert body["format"] == "json"
    assert body["think"] is False
    assert body["options"] == {"temperature": 0.0}
    messages = body["messages"]
    assert isinstance(messages, list)
    assert messages[1] == {"role": "user", "content": "PREGUNTA-SECRETA-03"}
    assert result.text == "RESPUESTA-SECRETA-03"
    assert "PREGUNTA-SECRETA-03" not in caplog.text
    assert "RESPUESTA-SECRETA-03" not in caplog.text
    assert "status=ok" in caplog.text


def test_connection_failure_is_actionable_and_hides_the_prompt() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        raise httpx.ConnectError("refused")

    provider = _provider(httpx.MockTransport(handler))

    with pytest.raises(LLMUnavailableError) as caught:
        provider.generate(_REQUEST)

    assert "Ollama no está disponible" in caught.value.message
    assert "Inicia Ollama" in caught.value.message
    assert "qwen2.5:7b" in caught.value.message
    assert "PREGUNTA-SECRETA-03" not in caught.value.message
    assert "Traceback" not in caught.value.message


def test_connect_timeout_tells_the_user_to_start_ollama() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        raise httpx.ConnectTimeout("connect timed out")

    provider = _provider(httpx.MockTransport(handler))

    with pytest.raises(LLMUnavailableError) as caught:
        provider.generate(_REQUEST)

    assert "Ollama no está disponible" in caught.value.message
    assert "Inicia Ollama" in caught.value.message
    assert "120" not in caught.value.message
    assert "PREGUNTA-SECRETA-03" not in caught.value.message


def test_timeout_is_a_stable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    provider = _provider(httpx.MockTransport(handler))

    with pytest.raises(LLMTimeoutError) as caught:
        provider.generate(_REQUEST)

    assert "RAG_LLM_TIMEOUT_SECONDS" in caught.value.message
    assert "PREGUNTA-SECRETA-03" not in caught.value.message


def test_http_error_does_not_echo_the_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(404, text="CONTEXTO-SECRETO-03")

    provider = _provider(httpx.MockTransport(handler))

    with pytest.raises(LLMResponseError) as caught:
        provider.generate(_REQUEST)

    assert "HTTP 404" in caught.value.message
    assert "ollama pull qwen2.5:7b" in caught.value.message
    assert "CONTEXTO-SECRETO-03" not in caught.value.message


def test_empty_content_is_a_controlled_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"message": {"content": "  "}})

    provider = _provider(httpx.MockTransport(handler))

    with pytest.raises(LLMResponseError):
        provider.generate(_REQUEST)


def test_streaming_is_declared_and_not_implemented() -> None:
    provider = OllamaLLMProvider(model="qwen2.5:7b")

    with pytest.raises(LLMStreamingNotAvailableError):
        provider.stream(_REQUEST)
