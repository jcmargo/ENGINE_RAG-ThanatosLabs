"""Hash de secretos de alta entropía (refresh y API keys)."""

from __future__ import annotations

import hashlib
import hmac
import secrets


def generate_opaque_token(nbytes: int = 32) -> str:
    """Genera un token opaco con al menos `nbytes` de aleatoriedad."""
    if nbytes < 32:
        message = "El token opaco debe usar al menos 32 bytes aleatorios"
        raise ValueError(message)
    return secrets.token_urlsafe(nbytes)


def hash_opaque_secret(secret: str) -> str:
    """SHA-256 hex del secreto. Apto para secretos de alta entropía."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def secrets_match(left: str, right: str) -> bool:
    """Comparación resistente a timing de dos cadenas."""
    return hmac.compare_digest(left, right)


API_KEY_PREFIX_MARKER = "rag_"
_API_KEY_PREFIX_LEN = 12


def generate_api_key_material() -> tuple[str, str, str]:
    """Devuelve (secreto completo, prefijo identificable, hash del secreto)."""
    body = secrets.token_urlsafe(32)
    full_secret = f"{API_KEY_PREFIX_MARKER}{body}"
    prefix = full_secret[:_API_KEY_PREFIX_LEN]
    return full_secret, prefix, hash_opaque_secret(full_secret)


def extract_api_key_prefix(bearer: str) -> str:
    """Extrae el prefijo identificable de un Bearer que empieza por rag_."""
    if not bearer.startswith(API_KEY_PREFIX_MARKER):
        message = "El Bearer no es una API key"
        raise ValueError(message)
    if len(bearer) < _API_KEY_PREFIX_LEN:
        message = "API key demasiado corta"
        raise ValueError(message)
    return bearer[:_API_KEY_PREFIX_LEN]
