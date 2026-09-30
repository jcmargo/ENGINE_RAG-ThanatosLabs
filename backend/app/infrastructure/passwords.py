"""Política Argon2id con parámetros explícitos. Vive fuera del dominio."""

from __future__ import annotations

from dataclasses import dataclass

from argon2 import PasswordHasher as Argon2PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.profiles import RFC_9106_LOW_MEMORY


@dataclass(frozen=True)
class Argon2Policy:
    """Parámetros explícitos de Argon2id."""

    time_cost: int = RFC_9106_LOW_MEMORY.time_cost
    memory_cost: int = RFC_9106_LOW_MEMORY.memory_cost
    parallelism: int = RFC_9106_LOW_MEMORY.parallelism
    hash_len: int = RFC_9106_LOW_MEMORY.hash_len
    salt_len: int = RFC_9106_LOW_MEMORY.salt_len


class PasswordHasher:
    """Hash y verificación Argon2id con rehash cuando cambia la política."""

    def __init__(self, policy: Argon2Policy | None = None) -> None:
        self._policy = policy if policy is not None else Argon2Policy()
        self._hasher = Argon2PasswordHasher(
            time_cost=self._policy.time_cost,
            memory_cost=self._policy.memory_cost,
            parallelism=self._policy.parallelism,
            hash_len=self._policy.hash_len,
            salt_len=self._policy.salt_len,
        )
        # Hash ficticio para igualar el coste cuando el email no existe.
        self._dummy_hash = self._hasher.hash("__engine_rag_dummy_password__")

    @property
    def policy(self) -> Argon2Policy:
        """Política activa de hash."""
        return self._policy

    def hash(self, password: str) -> str:
        """Devuelve el hash Argon2id de la contraseña."""
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        """Compara la contraseña con el hash. No revela el motivo del fallo."""
        try:
            return self._hasher.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def verify_or_dummy(self, password_hash: str | None, password: str) -> bool:
        """Verifica contra el hash real o uno ficticio si el usuario no existe."""
        target = password_hash if password_hash is not None else self._dummy_hash
        return self.verify(target, password)

    def needs_rehash(self, password_hash: str) -> bool:
        """Indica si el hash guardado no cumple la política actual."""
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except (InvalidHashError, VerificationError):
            return True
