"""Contrato sustituible del proveedor de lenguaje."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from app.generation.errors import LLMStreamingNotAvailableError


@dataclass(frozen=True)
class GenerationRequest:
    """Mensajes de sistema y de usuario. No incluye historial."""

    system_prompt: str
    user_prompt: str


@dataclass(frozen=True)
class GenerationResult:
    """Texto devuelto por el proveedor, todavía sin validar."""

    text: str


class LLMProvider(Protocol):
    """Generación síncrona. El streaming queda declarado para una etapa posterior."""

    @property
    def model_version(self) -> str:
        """Identificador versionado del modelo de lenguaje."""
        ...

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Devuelve la salida completa de una sola pregunta."""
        ...

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        """Previsto. Los proveedores de esta etapa no emiten tokens."""
        ...


def streaming_not_available(request: GenerationRequest) -> Iterator[str]:
    """Punto único del streaming todavía no implementado."""
    del request
    message = "El streaming está previsto y todavía no está implementado"
    raise LLMStreamingNotAvailableError(message)
