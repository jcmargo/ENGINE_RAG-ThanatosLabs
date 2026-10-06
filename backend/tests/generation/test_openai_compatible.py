"""API compatible con OpenAI, con transporte falso. La clave no se registra."""

import json
import logging

import httpx
import pytest

from app.generation.base import GenerationRequest
from app.generation.errors import LLMResponseError, LLMTimeoutError, LLMUnavailableError
from app.generation.openai_compatible import OpenAICompatibleProvider

_KEY = "sk-clave-secreta-99"
_REQUEST = GenerationRequest(system_prompt="sistema", user_prompt="PREGUNTA-SECRETA-03")


def _provider(transport: httpx.MockTransport) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        base_url="https://llm.example/v1",
        api_key=_KEY,
        model="modelo-externo",
        temperature=0.0,
        client=httpx.Client(transport=transport),
    )


def test_posts_json_chat_and_hides_the_key(caplog: pytest.LogCaptureFixture) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": '{"answer":"ok"}'}},
                ]
            },
        )

    provider = _provider(httpx.MockTransport(handler))
    with caplog.at_level(logging.DEBUG):
        result = provider.generate(_REQUEST)

    body = seen["body"]
    assert isinstance(body, dict)
    assert seen["path"] == "/v1/chat/completions"
    assert seen["authorization"] == f"Bearer {_KEY}"
    assert body["model"] == "modelo-externo"
    assert body["temperature"] == 0.0
    assert body["response_format"] == {"type": "json_object"}
    assert _KEY not in json.dumps(body)
    assert result.text == '{"answer":"ok"}'
    own_logs = " ".join(
        record.getMessage() for record in caplog.records if record.name.startswith("app.")
    )
    assert _KEY not in caplog.text
    assert "PREGUNTA-SECRETA-03" not in caplog.text
    assert "llm.example" not in own_logs
    assert _KEY not in own_logs


def test_non_200_is_a_stable_error_without_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(502, text=_KEY)

    provider = _provider(httpx.MockTransport(handler))

    with pytest.raises(LLMResponseError) as caught:
        provider.generate(_REQUEST)

    assert caught.value.message == "La API externa no pudo completar la respuesta"
    assert _KEY not in caught.value.message


def test_timeout_and_connection_failure_hide_the_key() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        del request
        raise httpx.ReadTimeout("timed out")

    def refused(request: httpx.Request) -> httpx.Response:
        del request
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMTimeoutError) as timed_out:
        _provider(httpx.MockTransport(timeout)).generate(_REQUEST)
    with pytest.raises(LLMUnavailableError) as unavailable:
        _provider(httpx.MockTransport(refused)).generate(_REQUEST)

    assert _KEY not in timed_out.value.message
    assert _KEY not in unavailable.value.message
    assert "llm.example" not in timed_out.value.message
    assert "llm.example" not in unavailable.value.message
