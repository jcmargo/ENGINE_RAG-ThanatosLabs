"""Calibración del umbral con multilingual-e5-base. Fuera de la suite por defecto."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.embeddings.sentence_transformer import SentenceTransformerEmbeddingProvider
from app.embeddings.tokens import HuggingFaceTokenCounter
from app.generation.base import GenerationRequest, GenerationResult
from app.generation.errors import LLMStreamingNotAvailableError
from app.generation.models import PUBLIC_ABSTENTION
from app.generation.service import GroundedRAGService
from app.retrieval.sample_search import SampleSearchService

pytestmark = pytest.mark.model

_CACHE = Path(__file__).resolve().parents[3] / "models" / "embeddings"
_SAMPLE = Path(__file__).resolve().parents[3] / "data" / "sample"
_THRESHOLD = 0.8

_KNOWN = (
    (
        "¿Cuántos días de descanso anual tengo?",
        "18 días hábiles",
        "politica-vacaciones.md",
    ),
    (
        "¿Cuál es el máximo reembolsable de taxi?",
        "40 USD",
        "politica-gastos.md",
    ),
    (
        "¿En cuánto tiempo se atienden los incidentes críticos?",
        "15 minutos",
        "procedimiento-soporte.md",
    ),
)

_OUT_OF_DOMAIN = (
    "¿Cuál es la capital de Japón?",
    "¿Quién ganó el mundial de fútbol de 2022?",
    "¿Cómo se calcula la derivada de x al cuadrado?",
    "¿Cuál es la receta de la paella valenciana?",
    "¿A qué temperatura hierve el agua a nivel del mar?",
    "¿Cuál es la capital de Francia?",
)


class _FailIfCalled:
    calls = 0

    @property
    def model_version(self) -> str:
        return "unused"

    def generate(self, request: GenerationRequest) -> GenerationResult:
        del request
        self.calls += 1
        message = "el umbral no debía llamar al modelo"
        raise AssertionError(message)

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        del request
        raise LLMStreamingNotAvailableError("no")


@pytest.fixture(scope="module")
def sample_search() -> SampleSearchService:
    provider = SentenceTransformerEmbeddingProvider(cache_dir=_CACHE)
    counter = HuggingFaceTokenCounter(cache_dir=_CACHE)
    return SampleSearchService(provider, _SAMPLE, counter)


@pytest.mark.parametrize(("question", "fact", "source"), _KNOWN)
def test_known_questions_pass_the_configured_threshold(
    sample_search: SampleSearchService,
    question: str,
    fact: str,
    source: str,
) -> None:
    hits = sample_search.search(question, top_k=3)
    evidence = tuple(hit for hit in hits if hit.result.score >= _THRESHOLD)

    assert evidence
    assert all(hit.source == source for hit in evidence)
    assert any(fact in hit.result.chunk.text for hit in evidence)


@pytest.mark.parametrize("question", _OUT_OF_DOMAIN)
def test_out_of_domain_questions_stay_under_the_threshold(
    sample_search: SampleSearchService,
    question: str,
) -> None:
    hits = sample_search.search(question, top_k=3)

    assert len(hits) >= 1
    assert all(hit.result.score < _THRESHOLD for hit in hits)


def test_japan_question_abstains_without_calling_the_llm(
    sample_search: SampleSearchService,
) -> None:
    llm = _FailIfCalled()
    service = GroundedRAGService(sample_search, llm, min_retrieval_score=_THRESHOLD)

    run = service.answer("¿Cuál es la capital de Japón?")

    assert llm.calls == 0
    assert run.llm_called is False
    assert run.answer.abstained is True
    assert run.answer.answer == PUBLIC_ABSTENTION
    assert run.answer.citations == ()
