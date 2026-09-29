"""Errores estables de la generación fundamentada."""


class GenerationError(Exception):
    """Error de generación con un código estable."""

    code = "generation_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class LLMUnavailableError(GenerationError):
    """Ollama no acepta la conexión."""

    code = "llm_unavailable"


class LLMTimeoutError(GenerationError):
    """Ollama no respondió dentro del tiempo configurado."""

    code = "llm_timeout"


class LLMResponseError(GenerationError):
    """Ollama respondió de una forma que no se puede usar."""

    code = "llm_response_error"


class GenerationFormatError(GenerationError):
    """La salida no es el JSON pedido."""

    code = "generation_format_error"


class UngroundedAnswerError(GenerationError):
    """La respuesta no se acepta porque no cita evidencia recuperada."""

    code = "ungrounded_answer"


class LLMStreamingNotAvailableError(GenerationError):
    """El streaming está previsto y todavía no emite tokens."""

    code = "streaming_not_available"
