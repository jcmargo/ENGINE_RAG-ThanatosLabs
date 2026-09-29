"""Proveedor de embeddings determinista, sin red."""

import math

import pytest

from app.embeddings.fake import FAKE_EMBEDDING_VERSION, DeterministicFakeEmbeddingProvider


def test_fake_vectors_are_deterministic_normalized_and_finite() -> None:
    provider = DeterministicFakeEmbeddingProvider(dimension=8)

    first = provider.embed_query("hola")
    second = provider.embed_query("hola")
    documents = provider.embed_documents(["hola", "adios"])
    norm = math.sqrt(sum(value * value for value in first))

    assert provider.dimension == 8
    assert provider.model_version == FAKE_EMBEDDING_VERSION
    assert first == second
    assert documents[0] == first
    assert documents[1] != first
    assert provider.embed_documents(()) == []
    assert norm == pytest.approx(1.0)
    assert all(math.isfinite(value) for value in first)


def test_fake_rejects_invalid_dimension() -> None:
    with pytest.raises(ValueError, match="dimensión"):
        DeterministicFakeEmbeddingProvider(dimension=0)
