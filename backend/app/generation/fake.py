"""Proveedor falso para respuestas, abstenciones, errores y citas inválidas."""

from collections.abc import Iterator, Sequence

from app.generation.base import (
    GenerationRequest,
    GenerationResult,
    streaming_not_available,
)
from app.generation.errors import GenerationError, LLMResponseError


class FakeLLMProvider:
    """Reproduce un guion de textos o errores, en orden."""

    def __init__(
        self,
        script: Sequence[str | GenerationError],
        *,
        model_version: str = "fake-llm",
    ) -> None:
        if not model_version.strip():
            message = "La versión del modelo falso no puede estar vacía"
            raise ValueError(message)
        self._script = list(script)
        self._model_version = model_version
        self.requests: list[GenerationRequest] = []

    @property
    def model_version(self) -> str:
        return self._model_version

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        if not self._script:
            message = "El proveedor falso no tiene más respuestas"
            raise LLMResponseError(message)
        step = self._script.pop(0)
        if isinstance(step, GenerationError):
            raise step
        return GenerationResult(text=step)

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        return streaming_not_available(request)
