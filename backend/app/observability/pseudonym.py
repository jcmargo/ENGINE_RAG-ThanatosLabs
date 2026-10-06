"""HMAC estable por entorno. No es un hash reversible por diccionario."""

from __future__ import annotations

import hashlib
import hmac


def pseudonym(secret: str, purpose: str, value: str) -> str:
    """Huella hex de 32 caracteres. El mismo secreto y valor producen la misma salida."""
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{purpose}\0{value}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return digest[:32]
