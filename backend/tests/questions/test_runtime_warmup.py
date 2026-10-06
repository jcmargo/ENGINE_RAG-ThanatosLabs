"""La precarga codifica una consulta antes de la primera pregunta."""

import pytest

from app.config import Environment, LogLevel, Settings
from app.questions.runtime import build_question_service


def _settings() -> Settings:
    return Settings(
        environment=Environment.DEVELOPMENT,
        log_level=LogLevel.INFO,
        api_host="127.0.0.1",
        api_port=8000,
    )


def test_prepare_encoder_embeds_one_query(monkeypatch: pytest.MonkeyPatch) -> None:
    queries: list[str] = []

    class Encoder:
        model_version = "test-model"

        @property
        def dimension(self) -> int:
            return 4

        def embed_query(self, text: str) -> list[float]:
            queries.append(text)
            return [0.0, 0.0, 0.0, 1.0]

    monkeypatch.setattr(
        "app.embeddings.sentence_transformer.SentenceTransformerEmbeddingProvider",
        lambda *_args, **_kwargs: Encoder(),
    )
    monkeypatch.setattr(
        "app.infrastructure.qdrant_store.QdrantVectorStore",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        "app.retrieval.service.RetrievalService",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        "app.generation.directory.RoutingLLMProvider",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        "app.generation.directory.ConfiguredLlmDirectory",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        "app.questions.catalog.SqlAlchemyCatalogOpener",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        "app.questions.runtime.QuestionService",
        lambda *_args, **_kwargs: "ready",
    )

    assert build_question_service(_settings(), object(), object(), prepare_encoder=True) == "ready"
    assert queries == ["preparado"]

    queries.clear()
    build_question_service(_settings(), object(), object())
    assert queries == []
