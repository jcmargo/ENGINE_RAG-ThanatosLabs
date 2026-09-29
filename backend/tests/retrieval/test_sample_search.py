"""Servicio local con el proveedor falso: no descarga modelos."""

from collections.abc import Sequence
from pathlib import Path

import pytest

from app.documents.chunking import WordTokenCounter
from app.embeddings.fake import DeterministicFakeEmbeddingProvider
from app.retrieval.errors import EmptySampleCorpusError
from app.retrieval.sample_search import SampleSearchService

_SAMPLE = Path(__file__).resolve().parents[3] / "data" / "sample"


class SpyProvider:
    """Registra el texto que envía el servicio, sin prefijos."""

    def __init__(self) -> None:
        self.documents: list[str] = []
        self.queries: list[str] = []

    @property
    def dimension(self) -> int:
        return 4

    @property
    def model_version(self) -> str:
        return "spy"

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.documents.extend(texts)
        return [[0.0, 0.0, 0.0, 1.0] for _text in texts]

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [0.0, 0.0, 0.0, 1.0]


def test_sample_service_indexes_facts_without_model_prefixes() -> None:
    provider = SpyProvider()
    service = SampleSearchService(provider, _SAMPLE, WordTokenCounter())

    hits = service.search("¿Cuántos días de descanso anual tengo?", top_k=2)
    again = service.search("¿Cuántos días de descanso anual tengo?", top_k=2)
    everything = service.search("corpus", top_k=50)
    blob = "\n".join(hit.result.chunk.text for hit in everything)

    assert len(hits) <= 2
    assert [hit.result.score for hit in hits] == sorted(
        (hit.result.score for hit in hits),
        reverse=True,
    )
    assert [hit.result.chunk.id for hit in hits] == [hit.result.chunk.id for hit in again]
    assert [hit.result.position for hit in everything] == list(range(1, len(everything) + 1))
    assert {hit.source for hit in everything} == {
        "politica-vacaciones.md",
        "politica-gastos.md",
        "procedimiento-soporte.md",
    }
    assert "18 días hábiles" in blob
    assert "40 USD" in blob
    assert "15 minutos" in blob
    assert provider.queries == [
        "¿Cuántos días de descanso anual tengo?",
        "¿Cuántos días de descanso anual tengo?",
        "corpus",
    ]
    assert all(not text.startswith("passage:") for text in provider.documents)
    assert all(not text.startswith("query:") for text in provider.documents)


def test_fake_provider_search_is_deterministic() -> None:
    service = SampleSearchService(
        DeterministicFakeEmbeddingProvider(),
        _SAMPLE,
        WordTokenCounter(),
    )

    first = service.search("vacaciones", top_k=3)
    second = service.search("vacaciones", top_k=3)

    assert [hit.result.chunk.id for hit in first] == [hit.result.chunk.id for hit in second]
    assert [hit.result.score for hit in first] == sorted(
        (hit.result.score for hit in first),
        reverse=True,
    )


def test_empty_sample_directory_raises(tmp_path: Path) -> None:
    (tmp_path / ".gitkeep").write_text("", encoding="utf-8")

    with pytest.raises(EmptySampleCorpusError) as caught:
        SampleSearchService(DeterministicFakeEmbeddingProvider(), tmp_path, WordTokenCounter())

    assert caught.value.code == "empty_sample_corpus"


def test_missing_sample_directory_raises(tmp_path: Path) -> None:
    missing = tmp_path / "no-existe"

    with pytest.raises(FileNotFoundError):
        SampleSearchService(DeterministicFakeEmbeddingProvider(), missing, WordTokenCounter())
