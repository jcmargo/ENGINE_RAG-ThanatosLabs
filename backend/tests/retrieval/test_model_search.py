"""Integración con multilingual-e5-base. Queda fuera de `uv run pytest`."""

import math
from pathlib import Path

import pytest

from app.documents.chunking import chunk_document
from app.documents.models import ExtractedDocument, ExtractedPage
from app.documents.versions import PARSER_VERSION
from app.embeddings.sentence_transformer import SentenceTransformerEmbeddingProvider
from app.embeddings.tokens import HuggingFaceTokenCounter
from app.retrieval.sample_search import SampleSearchService

pytestmark = pytest.mark.model

_CACHE = Path(__file__).resolve().parents[3] / "models" / "embeddings"
_SAMPLE = Path(__file__).resolve().parents[3] / "data" / "sample"


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


def test_real_vectors_have_coherent_dimension_unit_norm_and_finite_values(
    embedding_provider: SentenceTransformerEmbeddingProvider,
) -> None:
    same_text = "18 días hábiles de vacaciones"
    documents = embedding_provider.embed_documents([same_text])
    query = embedding_provider.embed_query(same_text)

    assert embedding_provider.dimension == 768
    assert len(documents[0]) == embedding_provider.dimension
    assert len(query) == embedding_provider.dimension
    assert documents[0] != query
    for vector in (documents[0], query):
        norm = math.sqrt(sum(value * value for value in vector))
        assert norm == pytest.approx(1.0, abs=1e-3)
        assert all(math.isfinite(value) for value in vector)


def test_vacation_question_retrieves_the_known_fact(
    sample_search: SampleSearchService,
) -> None:
    hits = sample_search.search("¿Cuántos días de descanso anual tengo?", top_k=3)

    assert any(
        "18 días hábiles" in hit.result.chunk.text and hit.source == "politica-vacaciones.md"
        for hit in hits
    )


@pytest.mark.parametrize(
    ("question", "fact", "source"),
    [
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
    ],
)
def test_equivalent_questions_retrieve_expense_and_support_facts(
    sample_search: SampleSearchService,
    question: str,
    fact: str,
    source: str,
) -> None:
    hits = sample_search.search(question, top_k=3)

    assert any(fact in hit.result.chunk.text and hit.source == source for hit in hits)


def test_chunker_respects_real_tokenizer_limit(token_counter: HuggingFaceTokenCounter) -> None:
    words = [f"palabra{index}" for index in range(180)]
    document = ExtractedDocument(
        name="largo.txt",
        mime_type="text/plain",
        pages=(ExtractedPage(number=1, text=" ".join(words)),),
        parser_version=PARSER_VERSION,
    )

    chunks = chunk_document(
        document,
        document_hash="hash",
        document_version="v1",
        counter=token_counter,
        max_tokens=40,
        overlap_tokens=8,
    )
    blob = " ".join(chunk.text for chunk in chunks)

    assert token_counter.count("hábiles") == 3
    assert len(chunks) > 1
    assert all(token_counter.count(chunk.text) <= 40 for chunk in chunks)
    assert words[0] in blob
    assert words[-1] in blob
