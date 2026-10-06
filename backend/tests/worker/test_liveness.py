"""El pulso del worker solo existe cuando Compose lo configura."""

from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from app.worker.runtime import note_liveness


def test_liveness_is_silent_without_configuration(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("RAG_WORKER_HEARTBEAT_PATH", raising=False)
    note_liveness()


def test_liveness_writes_the_configured_file(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    target = tmp_path / "pulse"
    monkeypatch.setenv("RAG_WORKER_HEARTBEAT_PATH", str(target))
    note_liveness()
    assert target.read_text(encoding="ascii") == "ok"
