"""Prefijos E5, lotes y carga perezosa, sin descargar el modelo."""

import logging
from collections.abc import Sequence

import pytest

from app.embeddings.sentence_transformer import (
    PASSAGE_PREFIX,
    QUERY_PREFIX,
    SentenceTransformerEmbeddingProvider,
)


class RecordingEncoder:
    """Sustituye al modelo y conserva el texto que habría recibido."""

    def __init__(self, dimension: int = 4) -> None:
        self._dimension = dimension
        self.inputs: list[tuple[str, ...]] = []
        self.prompts: list[str] = []
        self.batch_sizes: list[int] = []
        self.normalize: list[bool] = []
        self.show_progress: list[bool] = []

    def get_embedding_dimension(self) -> int:
        return self._dimension

    def encode(
        self,
        inputs: Sequence[str],
        *,
        batch_size: int,
        normalize_embeddings: bool,
        convert_to_numpy: bool,
        show_progress_bar: bool,
        prompt: str,
    ) -> list[list[float]]:
        del convert_to_numpy
        self.inputs.append(tuple(inputs))
        self.prompts.append(prompt)
        self.batch_sizes.append(batch_size)
        self.normalize.append(normalize_embeddings)
        self.show_progress.append(show_progress_bar)
        return [[float(index), 0.0, 0.0, 1.0] for index, _text in enumerate(inputs)]


def test_constructor_does_not_load_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(model_name: str, cache_dir: object) -> RecordingEncoder:
        raise AssertionError(model_name or cache_dir)

    monkeypatch.setattr(
        "app.embeddings.sentence_transformer._load_sentence_transformer",
        fail,
    )
    provider = SentenceTransformerEmbeddingProvider()

    assert provider.model_version == "intfloat/multilingual-e5-base"
    assert provider.embed_documents(()) == []


def test_adapter_prefixes_passages_and_queries_before_the_model() -> None:
    encoder = RecordingEncoder()
    provider = SentenceTransformerEmbeddingProvider(encoder=encoder, batch_size=8)

    documents = provider.embed_documents(["vacaciones", "gastos"])
    query = provider.embed_query("¿Cuántos días de descanso anual tengo?")

    assert encoder.inputs == [
        (f"{PASSAGE_PREFIX}vacaciones", f"{PASSAGE_PREFIX}gastos"),
        (f"{QUERY_PREFIX}¿Cuántos días de descanso anual tengo?",),
    ]
    assert encoder.inputs[0][0] == "passage: vacaciones"
    assert encoder.inputs[1][0] == "query: ¿Cuántos días de descanso anual tengo?"
    assert encoder.prompts == ["", ""]
    assert encoder.batch_sizes == [8, 8]
    assert encoder.normalize == [True, True]
    assert encoder.show_progress == [False, False]
    assert documents[0][0] == 0.0
    assert documents[1][0] == 1.0
    assert query[0] == 0.0
    assert len(query) == provider.dimension


def test_log_records_metadata_without_document_text(caplog: pytest.LogCaptureFixture) -> None:
    secret = "secreto-no-registrable"
    provider = SentenceTransformerEmbeddingProvider(encoder=RecordingEncoder(), batch_size=4)

    with caplog.at_level(logging.INFO, logger="app.embeddings.sentence_transformer"):
        provider.embed_documents([secret])
        provider.embed_query(secret)

    assert secret not in caplog.text
    assert "passage:" not in caplog.text
    assert "query:" not in caplog.text
    assert "intfloat/multilingual-e5-base" in caplog.text
    assert "dimension=4" in caplog.text
    assert "count=1" in caplog.text
    assert "duration_ms=" in caplog.text


def test_rejects_non_positive_batch_size() -> None:
    with pytest.raises(ValueError, match="batch_size"):
        SentenceTransformerEmbeddingProvider(batch_size=0)
