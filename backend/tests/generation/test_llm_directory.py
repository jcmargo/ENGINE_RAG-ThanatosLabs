"""Lista y elección de modelo con Ollama falso. No abre un puerto."""

import json

import httpx
import pytest
from pydantic import SecretStr

from app.config import Environment, LogLevel, Settings
from app.domain.errors import InvalidStateError
from app.generation.base import GenerationRequest
from app.generation.directory import (
    ConfiguredLlmDirectory,
    RoutingLLMProvider,
    bind_llm_choice,
    reset_llm_choice,
)
from app.generation.errors import LLMUnavailableError

_KEY = "sk-clave-secreta-99"


def _settings(*, external: bool) -> Settings:
    return Settings(
        environment=Environment.TEST,
        log_level=LogLevel.INFO,
        api_host="127.0.0.1",
        api_port=8000,
        llm_model="qwen2.5:7b",
        external_llm_base_url="https://llm.example/v1" if external else "",
        external_llm_api_key=SecretStr(_KEY if external else ""),
        external_llm_model="modelo-externo" if external else "",
    )


def _directory(tags: list[str], *, external: bool) -> ConfiguredLlmDirectory:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": name} for name in tags]})
        if request.url.path.endswith("/chat/completions"):
            authorization = request.headers.get("authorization")
            if authorization != f"Bearer {_KEY}":
                return httpx.Response(401, text=_KEY)
            body = json.loads(request.content.decode("utf-8"))
            assert body["temperature"] == 0.0
            assert _KEY not in json.dumps(body)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"answer":"ok"}'}}]},
            )
        body = json.loads(request.content.decode("utf-8"))
        assert isinstance(body, dict)
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": body["model"]}},
        )

    return ConfiguredLlmDirectory(
        _settings(external=external),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_lists_installed_models_and_omits_an_unconfigured_external_api() -> None:
    directory = _directory(["qwen3.5:2b", "qwen2.5:7b"], external=False)

    listed = directory.list_models()

    assert [item.id for item in listed] == ["ollama:qwen2.5:7b", "ollama:qwen3.5:2b"]
    assert all(item.provider == "ollama" for item in listed)
    with pytest.raises(InvalidStateError):
        directory.ensure("external")


def test_external_entry_hides_the_key_and_the_url() -> None:
    directory = _directory(["qwen3.5:2b"], external=True)

    listed = directory.list_models()
    rendered = " ".join(f"{item.id} {item.label} {item.provider}" for item in listed)

    assert listed[-1].id == "external"
    assert listed[-1].label == "modelo-externo"
    assert _KEY not in rendered
    assert "llm.example" not in rendered
    assert "https://" not in rendered


def test_unknown_model_is_rejected() -> None:
    directory = _directory(["qwen3.5:2b"], external=False)

    with pytest.raises(InvalidStateError) as caught:
        directory.ensure("ollama:qwen2.5:7b")

    assert caught.value.message == "El modelo no está disponible"
    with pytest.raises(InvalidStateError):
        directory.ensure("otro")


def test_router_sends_the_selected_ollama_model() -> None:
    directory = _directory(["qwen2.5:7b", "qwen3.5:2b"], external=False)
    router = RoutingLLMProvider(directory)
    token = bind_llm_choice("ollama:qwen3.5:2b")
    try:
        assert router.model_version == "qwen3.5:2b"
        result = router.generate(GenerationRequest(system_prompt="sistema", user_prompt="hola"))
    finally:
        reset_llm_choice(token)

    assert result.text == "qwen3.5:2b"


def test_router_calls_the_external_api_without_logging_the_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    directory = _directory(["qwen2.5:7b"], external=True)
    router = RoutingLLMProvider(directory)
    token = bind_llm_choice("external")
    try:
        with caplog.at_level("DEBUG"):
            result = router.generate(
                GenerationRequest(system_prompt="sistema", user_prompt="PREGUNTA-SECRETA-03")
            )
    finally:
        reset_llm_choice(token)

    own_logs = " ".join(
        record.getMessage() for record in caplog.records if record.name.startswith("app.")
    )
    assert result.text == '{"answer":"ok"}'
    assert _KEY not in caplog.text
    assert "PREGUNTA-SECRETA-03" not in caplog.text
    assert _KEY not in own_logs
    assert "llm.example" not in own_logs


def test_ollama_outage_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        raise httpx.ConnectError("refused")

    directory = ConfiguredLlmDirectory(
        _settings(external=False),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(LLMUnavailableError) as caught:
        directory.list_models()

    assert "127.0.0.1" not in caught.value.message
