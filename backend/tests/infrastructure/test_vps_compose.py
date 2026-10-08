"""El overlay del VPS publica solo HTTP y HTTPS."""

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


def test_vps_overlay_publishes_http_and_https_only() -> None:
    root = repository_root()
    text = Path(root / "infra" / "compose.vps.yml").read_text(encoding="utf-8")
    gateway = _service_block(text, "gateway")

    assert "80:80" in gateway
    assert "443:443" in gateway
    assert "!override" in gateway
    assert "127.0.0.1:${RAG_GATEWAY_PORT:-8080}:8080" not in text
    assert "8080:8080" not in text
    assert "NET_BIND_SERVICE" in gateway
    assert "172.28.0.1:11434" in text
    assert "172.28.0.0/24" in text
    assert "engine_rag_caddy" in text
    for published in ("5432:5432", "6333:6333", "8000:8000", "11434:11434"):
        assert published not in text
    for name in ("api", "worker", "init-data", "backup"):
        assert "ports:" not in _service_block(text, name)
    for name in ("postgres", "qdrant", "migrate"):
        assert f"  {name}:" not in text


def test_public_caddy_enables_https_and_keeps_local_template() -> None:
    root = repository_root()
    public = Path(root / "infra" / "Caddyfile.public").read_text(encoding="utf-8")
    local = Path(root / "infra" / "Caddyfile").read_text(encoding="utf-8")
    entry = Path(root / "infra" / "gateway-entrypoint.sh").read_text(encoding="utf-8")

    assert "{$RAG_DOMAIN}" in public
    assert "Strict-Transport-Security" in public
    assert "auto_https off" not in public
    assert ":8080" in local
    assert "auto_https off" in local
    assert "RAG_TLS_MODE:-local" in entry
    assert "XDG_DATA_HOME=/data" in entry
    assert "Caddyfile.public" in entry
