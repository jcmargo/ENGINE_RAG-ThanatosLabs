"""Calibración del umbral con multilingual-e5-base. Fuera de la suite por defecto."""

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.domain.evidence_policy import DEFAULT_LEXICAL_FLOOR
from app.embeddings.sentence_transformer import SentenceTransformerEmbeddingProvider
from app.embeddings.tokens import HuggingFaceTokenCounter
from app.generation.base import GenerationRequest, GenerationResult
from app.generation.errors import LLMStreamingNotAvailableError
from app.generation.models import PUBLIC_ABSTENTION
from app.generation.service import GroundedRAGService, _select_hits
from app.retrieval.sample_search import LocalSearchHit, SampleSearchService

pytestmark = pytest.mark.model

_CACHE = Path(__file__).resolve().parents[3] / "models" / "embeddings"
_SAMPLE = Path(__file__).resolve().parents[3] / "data" / "sample"
_THRESHOLD = 0.857

_POLICY_FILES = (
    "politica-vacaciones.md",
    "politica-gastos.md",
    "procedimiento-soporte.md",
)

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

_POLICY_PARAPHRASES = (
    (
        "¿Cuántos días hábiles de vacaciones otorga la empresa al año?",
        "18 días hábiles",
        "politica-vacaciones.md",
    ),
    (
        "¿Se pueden dividir las vacaciones en dos periodos?",
        "dos periodos",
        "politica-vacaciones.md",
    ),
    (
        "¿Con qué antelación se envía la solicitud de vacaciones?",
        "antelación",
        "politica-vacaciones.md",
    ),
    (
        "¿Cuál es el tope reembolsable de un trayecto en taxi?",
        "40 USD",
        "politica-gastos.md",
    ),
    (
        "¿Quién aprueba por escrito un gasto de taxi por encima del máximo?",
        "por escrito",
        "politica-gastos.md",
    ),
    (
        "¿Durante cuánto tiempo se conservan los comprobantes?",
        "ejercicio fiscal",
        "politica-gastos.md",
    ),
    (
        "¿Cuál es el plazo de atención de los incidentes críticos?",
        "15 minutos",
        "procedimiento-soporte.md",
    ),
    (
        "¿El equipo de soporte registra el número de ticket en la herramienta interna?",
        "herramienta interna",
        "procedimiento-soporte.md",
    ),
    (
        "¿Cómo se gestionan las incidencias ordinarias en soporte?",
        "cola del servicio",
        "procedimiento-soporte.md",
    ),
)

_NEAR_MISS = (
    (
        "¿Cuántos días de vacaciones tiene un contratista?",
        "politica-vacaciones.md",
        "contratista",
    ),
)

_POLICY_ABSTENTIONS = (
    "¿Cuál es el máximo reembolsable de un avión?",
    "¿En cuántos minutos se atienden las incidencias ordinarias?",
)

_OUT_OF_DOMAIN = (
    "¿Cuál es la capital de Japón?",
    "¿Quién ganó el mundial de fútbol de 2022?",
    "¿Cómo se calcula la derivada de x al cuadrado?",
    "¿Cuál es la receta de la paella valenciana?",
    "¿A qué temperatura hierve el agua a nivel del mar?",
    "¿Cuál es la capital de Francia?",
)

_POLICY_OUT_OF_DOMAIN = (
    *_OUT_OF_DOMAIN,
    "¿Cuál es el salario base de un empleado a tiempo completo?",
    "¿Puedo hacer teletrabajo tres días a la semana?",
    "¿Cuántos días de licencia por paternidad otorga la empresa?",
)

_ALGEBRA = (
    "¿Cómo se calcula la derivada de x al cuadrado?",
    "¿Qué es un espacio vectorial?",
    "¿Qué es un sistema de ecuaciones lineales?",
    "¿En qué consiste el método de Gauss?",
)

_MIXED_OUT_OF_DOMAIN = (
    "¿Cuál es la receta de la paella valenciana?",
    "¿Cuál es la capital de Japón?",
    "¿Cuál es la capital de Francia?",
    "¿Quién ganó el mundial de fútbol de 2022?",
    "¿A qué temperatura hierve el agua a nivel del mar?",
)


def _format_hits(hits: tuple[LocalSearchHit, ...]) -> str:
    return ", ".join(f"{hit.source}={hit.result.score:.6f}" for hit in hits)


def _evidence(question: str, hits: tuple[LocalSearchHit, ...]) -> tuple[LocalSearchHit, ...]:
    return _select_hits(
        question,
        hits,
        semantic_floor=_THRESHOLD,
        lexical_floor=DEFAULT_LEXICAL_FLOOR,
    )


def _local_pdf_name() -> str | None:
    pdfs = sorted(_SAMPLE.glob("*.pdf"))
    if len(pdfs) != 1:
        return None
    return pdfs[0].name


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
def embedding_provider() -> SentenceTransformerEmbeddingProvider:
    return SentenceTransformerEmbeddingProvider(cache_dir=_CACHE)


@pytest.fixture(scope="module")
def token_counter() -> HuggingFaceTokenCounter:
    return HuggingFaceTokenCounter(cache_dir=_CACHE)


@pytest.fixture(scope="module")
def sample_search(
    embedding_provider: SentenceTransformerEmbeddingProvider,
    token_counter: HuggingFaceTokenCounter,
) -> SampleSearchService:
    return SampleSearchService(embedding_provider, _SAMPLE, token_counter)


@pytest.fixture(scope="module")
def policies_search(
    embedding_provider: SentenceTransformerEmbeddingProvider,
    token_counter: HuggingFaceTokenCounter,
    tmp_path_factory: pytest.TempPathFactory,
) -> SampleSearchService:
    dest = tmp_path_factory.mktemp("policies")
    for name in _POLICY_FILES:
        shutil.copy2(_SAMPLE / name, dest / name)
    return SampleSearchService(embedding_provider, dest, token_counter)


@pytest.fixture(scope="module")
def sample_pdf_name() -> str:
    name = _local_pdf_name()
    if name is None:
        pytest.skip("no hay un único PDF local en data/sample")
    return name


@pytest.mark.parametrize(("question", "fact", "source"), _KNOWN)
def test_known_questions_pass_the_configured_threshold(
    sample_search: SampleSearchService,
    question: str,
    fact: str,
    source: str,
) -> None:
    hits = sample_search.search(question, top_k=3)
    evidence = tuple(hit for hit in hits if hit.result.score >= _THRESHOLD)

    assert evidence, _format_hits(hits)
    assert all(hit.source == source for hit in evidence), _format_hits(evidence)
    assert any(fact in hit.result.chunk.text for hit in evidence), _format_hits(evidence)


@pytest.mark.parametrize("question", _OUT_OF_DOMAIN)
def test_out_of_domain_questions_stay_under_the_threshold(
    sample_search: SampleSearchService,
    question: str,
) -> None:
    hits = sample_search.search(question, top_k=3)

    assert len(hits) >= 1
    assert all(hit.result.score < _THRESHOLD for hit in hits), _format_hits(hits)


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


@pytest.mark.parametrize(("question", "fact", "source"), _POLICY_PARAPHRASES)
def test_policy_paraphrases_pass_the_configured_threshold(
    policies_search: SampleSearchService,
    question: str,
    fact: str,
    source: str,
) -> None:
    hits = policies_search.search(question, top_k=3)
    evidence = _evidence(question, hits)

    assert evidence, _format_hits(hits)
    assert all(hit.source == source for hit in evidence), _format_hits(evidence)
    assert any(fact in hit.result.chunk.text for hit in evidence), _format_hits(evidence)


@pytest.mark.parametrize(("question", "source", "absent"), _NEAR_MISS)
def test_near_miss_policy_questions_do_not_invent_absent_words(
    policies_search: SampleSearchService,
    question: str,
    source: str,
    absent: str | None,
) -> None:
    hits = policies_search.search(question, top_k=3)
    evidence = tuple(hit for hit in hits if hit.result.score >= _THRESHOLD)

    for hit in evidence:
        assert hit.source == source, _format_hits(evidence)
        if absent is not None:
            assert absent not in hit.result.chunk.text, _format_hits(evidence)


@pytest.mark.parametrize("question", _POLICY_ABSTENTIONS)
def test_near_miss_quantity_and_measure_questions_abstain(
    policies_search: SampleSearchService,
    question: str,
) -> None:
    hits = policies_search.search(question, top_k=3)
    assert _evidence(question, hits) == ()

    llm = _FailIfCalled()
    service = GroundedRAGService(policies_search, llm, min_retrieval_score=_THRESHOLD)
    run = service.answer(question)

    assert llm.calls == 0
    assert run.llm_called is False
    assert run.answer.abstained is True
    assert run.answer.answer == PUBLIC_ABSTENTION
    assert run.answer.citations == ()


@pytest.mark.parametrize("question", _POLICY_OUT_OF_DOMAIN)
def test_policy_out_of_domain_questions_stay_under_the_threshold(
    policies_search: SampleSearchService,
    question: str,
) -> None:
    hits = policies_search.search(question, top_k=3)

    assert len(hits) >= 1
    assert all(hit.result.score < _THRESHOLD for hit in hits), _format_hits(hits)


@pytest.mark.parametrize("question", _POLICY_OUT_OF_DOMAIN)
def test_policy_out_of_domain_questions_abstain_without_calling_the_llm(
    policies_search: SampleSearchService,
    question: str,
) -> None:
    llm = _FailIfCalled()
    service = GroundedRAGService(policies_search, llm, min_retrieval_score=_THRESHOLD)

    run = service.answer(question)

    assert llm.calls == 0
    assert run.llm_called is False
    assert run.answer.abstained is True
    assert run.answer.answer == PUBLIC_ABSTENTION
    assert run.answer.citations == ()


@pytest.mark.parametrize(("question", "fact", "source"), _KNOWN)
def test_known_questions_on_mixed_index_keep_markdown_evidence(
    sample_search: SampleSearchService,
    sample_pdf_name: str,
    question: str,
    fact: str,
    source: str,
) -> None:
    del sample_pdf_name
    hits = sample_search.search(question, top_k=5)
    evidence = tuple(hit for hit in hits if hit.result.score >= _THRESHOLD)

    assert evidence, _format_hits(hits)
    assert all(hit.source == source for hit in evidence), _format_hits(evidence)
    assert hits[0].source == source, _format_hits(hits)
    assert any(fact in hit.result.chunk.text for hit in evidence), _format_hits(evidence)


@pytest.mark.parametrize("question", _ALGEBRA)
def test_algebra_questions_rank_the_local_pdf_first(
    sample_search: SampleSearchService,
    sample_pdf_name: str,
    question: str,
) -> None:
    hits = sample_search.search(question, top_k=3)

    assert hits, "sin resultados"
    assert hits[0].source == sample_pdf_name, _format_hits(hits)


@pytest.mark.parametrize("question", _ALGEBRA)
def test_algebra_questions_abstain_when_below_threshold(
    sample_search: SampleSearchService,
    sample_pdf_name: str,
    question: str,
) -> None:
    hits = sample_search.search(question, top_k=3)

    assert hits, "sin resultados"
    assert hits[0].source == sample_pdf_name, _format_hits(hits)
    evidence = _evidence(question, hits)
    if evidence:
        assert all(hit.source == sample_pdf_name for hit in evidence), _format_hits(evidence)
        return

    llm = _FailIfCalled()
    service = GroundedRAGService(sample_search, llm, min_retrieval_score=_THRESHOLD)
    run = service.answer(question)

    assert llm.calls == 0
    assert run.llm_called is False
    assert run.answer.abstained is True
    assert run.answer.answer == PUBLIC_ABSTENTION
    assert run.answer.citations == ()


@pytest.mark.parametrize("question", _MIXED_OUT_OF_DOMAIN)
def test_mixed_out_of_domain_questions_stay_under_the_threshold(
    sample_search: SampleSearchService,
    sample_pdf_name: str,
    question: str,
) -> None:
    del sample_pdf_name
    hits = sample_search.search(question, top_k=3)

    assert len(hits) >= 1
    assert all(hit.result.score < _THRESHOLD for hit in hits), _format_hits(hits)


@pytest.mark.parametrize("question", _MIXED_OUT_OF_DOMAIN)
def test_mixed_out_of_domain_questions_abstain_without_calling_the_llm(
    sample_search: SampleSearchService,
    sample_pdf_name: str,
    question: str,
) -> None:
    del sample_pdf_name
    llm = _FailIfCalled()
    service = GroundedRAGService(sample_search, llm, min_retrieval_score=_THRESHOLD)

    run = service.answer(question)

    assert llm.calls == 0
    assert run.llm_called is False
    assert run.answer.abstained is True
    assert run.answer.answer == PUBLIC_ABSTENTION
    assert run.answer.citations == ()
