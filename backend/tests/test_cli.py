"""Arranque local del proceso API."""

import subprocess
import sys

import pytest

from app.__main__ import child_environment, main, server_command
from app.config import Environment, LogLevel, Settings


def _runtime_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_ENVIRONMENT", "development")
    monkeypatch.setenv("RAG_LOG_LEVEL", "INFO")
    monkeypatch.setenv("RAG_API_HOST", "127.0.0.1")
    monkeypatch.setenv("RAG_API_PORT", "8000")


def test_server_command_targets_configured_endpoint() -> None:
    settings = Settings(
        environment=Environment.DEVELOPMENT,
        log_level=LogLevel.INFO,
        api_host="127.0.0.1",
        api_port=8123,
    )

    assert server_command(settings) == [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8123",
    ]


def test_main_exits_with_server_status(monkeypatch: pytest.MonkeyPatch) -> None:
    _runtime_environment(monkeypatch)

    def fake_run(
        command: list[str],
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert command[3] == "app.main:app"
        assert check is False
        assert env is not None
        assert env["RAG_WARM_EMBEDDINGS"] == "true"
        return subprocess.CompletedProcess(command, 3)

    monkeypatch.setattr("app.__main__.subprocess.run", fake_run)
    with pytest.raises(SystemExit) as caught:
        main()

    assert caught.value.code == 3


def test_main_exits_130_on_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    _runtime_environment(monkeypatch)

    def fake_run(
        command: list[str],
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert command
        assert check is False
        assert env is not None
        raise KeyboardInterrupt

    monkeypatch.setattr("app.__main__.subprocess.run", fake_run)
    with pytest.raises(SystemExit) as caught:
        main()

    assert caught.value.code == 130


def test_child_environment_preloads_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAG_WARM_EMBEDDINGS", raising=False)

    assert child_environment()["RAG_WARM_EMBEDDINGS"] == "true"


def test_child_environment_keeps_an_explicit_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_WARM_EMBEDDINGS", "false")

    assert child_environment()["RAG_WARM_EMBEDDINGS"] == "false"
