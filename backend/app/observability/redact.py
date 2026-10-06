"""Quita secretos y centinelas de cualquier línea antes de escribirla."""

from __future__ import annotations

import re

_REDACTED = "[redacted]"
_PATTERNS = (
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(?:api_key|password|secret|token)\s*=\s*\S+"),
    re.compile(r"(?i)\bBearer\s+\S+"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"(?i)\b(?:authorization|cookie)\s*:\s*\S+"),
)
_sentinels: set[str] = set()


def register_sentinel(value: str) -> None:
    """Marca un texto documental que no puede aparecer en logs."""
    cleaned = value.strip()
    if len(cleaned) >= 8:
        _sentinels.add(cleaned)


def clear_sentinels() -> None:
    """Vacía los centinelas. Lo usan las pruebas."""
    _sentinels.clear()


def redact(text: str) -> str:
    """Sustituye secretos y centinelas. El resto del mensaje se conserva."""
    cleaned = text
    for pattern in _PATTERNS:
        cleaned = pattern.sub(_REDACTED, cleaned)
    for sentinel in _sentinels:
        if sentinel in cleaned:
            cleaned = cleaned.replace(sentinel, _REDACTED)
    return cleaned
