"""El Compose del producto no publica bases ni la API."""

from __future__ import annotations

from pathlib import Path

from app.config import repository_root


def _service_block(text: str, name: str) -> str:
    marker = f"  {name}:"
    start = text.index(marker)
    rest = text[start + len(marker) :]
    lines = rest.splitlines()
    body = [marker]
    for line in lines:
        if line.startswith("  ") and not line.startswith("    ") and line.endswith(":"):
            break
        body.append(line)
    return "\n".join(body)


def test_only_the_gateway_publishes_a_port() -> None:
    text = Path(repository_root() / "infra" / "compose.yml").read_text(encoding="utf-8")

    assert "127.0.0.1:${RAG_GATEWAY_PORT:-8080}:8080" in _service_block(text, "gateway")
    for name in ("postgres", "qdrant", "api", "worker", "migrate", "init-data", "backup"):
        assert "ports:" not in _service_block(text, name)
    backup = _service_block(text, "backup")
    assert 'profiles: ["backup"]' in backup
    assert 'restart: "no"' in backup
    assert "app.cli.backup" in backup
    assert "internal: true" in text
    assert 'user: "10001:10001"' in _service_block(text, "api")
    assert 'user: "10001:10001"' in _service_block(text, "worker")
    assert 'restart: "no"' in _service_block(text, "migrate")
    assert "service_completed_successfully" in _service_block(text, "api")
    assert "python -m alembic" not in _service_block(text, "api")
    assert "upgrade" in _service_block(text, "migrate")
