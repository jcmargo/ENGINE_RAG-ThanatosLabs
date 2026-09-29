"""Generación local fundamentada."""

from app.generation.base import (
    GenerationRequest,
    GenerationResult,
    LLMProvider,
)
from app.generation.fake import FakeLLMProvider
from app.generation.models import (
    PROMPT_VERSION,
    PUBLIC_ABSTENTION,
    RETRIEVAL_VERSION,
    Citation,
    GroundedAnswer,
    GroundedRun,
)
from app.generation.ollama import (
    DEFAULT_LLM_MODEL,
    DEFAULT_OLLAMA_BASE_URL,
    OllamaLLMProvider,
)
from app.generation.service import GroundedRAGService

__all__ = [
    "DEFAULT_LLM_MODEL",
    "DEFAULT_OLLAMA_BASE_URL",
    "PROMPT_VERSION",
    "PUBLIC_ABSTENTION",
    "RETRIEVAL_VERSION",
    "Citation",
    "FakeLLMProvider",
    "GenerationRequest",
    "GenerationResult",
    "GroundedAnswer",
    "GroundedRAGService",
    "GroundedRun",
    "LLMProvider",
    "OllamaLLMProvider",
]
