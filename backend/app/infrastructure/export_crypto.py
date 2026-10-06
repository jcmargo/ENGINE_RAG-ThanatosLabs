"""Cifrado AES-GCM del paquete de exportación. El token no se guarda."""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def encrypt_package(plaintext: bytes) -> tuple[str, str, bytes]:
    """Devuelve el token en claro, el nonce y el ciphertext."""
    key = AESGCM.generate_key(bit_length=256)
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    return key.hex(), nonce.hex(), ciphertext


def decrypt_package(token: str, nonce_hex: str, ciphertext: bytes) -> bytes:
    """Descifra con el token que solo tuvo el cliente."""
    aesgcm = AESGCM(bytes.fromhex(token))
    return aesgcm.decrypt(bytes.fromhex(nonce_hex), ciphertext, None)
