"""El paquete se descifra solo con el token que se entregó una vez."""

from __future__ import annotations

import pytest
from cryptography.exceptions import InvalidTag

from app.infrastructure.export_crypto import decrypt_package, encrypt_package


def test_roundtrip_and_wrong_token() -> None:
    token, nonce, ciphertext = encrypt_package(b"paquete")
    assert decrypt_package(token, nonce, ciphertext) == b"paquete"
    with pytest.raises(InvalidTag):
        decrypt_package("00" * 32, nonce, ciphertext)
