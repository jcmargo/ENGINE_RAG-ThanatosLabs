"""Índice en memoria: dimensión, top_k y desempate."""

import pytest

from app.documents.models import Chunk
from app.retrieval.errors import DimensionMismatchError
from app.retrieval.memory import InMemoryVectorIndex


def _chunk(chunk_id: str, text: str = "texto") -> Chunk:
    return Chunk(
        id=chunk_id,
        text=text,
        page_start=1,
        page_end=1,
        section=None,
        position=0,
        chunker_version="chunker-v1",
    )


def test_rejects_vector_with_wrong_dimension() -> None:
    index = InMemoryVectorIndex(2)

    with pytest.raises(DimensionMismatchError, match="dimensión") as caught:
        index.add(_chunk("a"), [1.0, 0.0, 0.0])

    assert caught.value.code == "dimension_mismatch"
    with pytest.raises(DimensionMismatchError, match="espera 2"):
        index.search([1.0], top_k=1)


def test_search_limits_orders_and_breaks_ties_by_id() -> None:
    index = InMemoryVectorIndex(2)
    index.add(_chunk("low"), [0.0, 1.0])
    index.add(_chunk("b"), [1.0, 0.0])
    index.add(_chunk("a"), [1.0, 0.0])

    limited = index.search([1.0, 0.0], top_k=1)
    ranked = index.search([1.0, 0.0], top_k=5)
    again = index.search([1.0, 0.0], top_k=5)

    assert [item.chunk.id for item in limited] == ["a"]
    assert limited[0].position == 1
    assert [item.chunk.id for item in ranked] == ["a", "b", "low"]
    assert [item.position for item in ranked] == [1, 2, 3]
    assert ranked[0].score == pytest.approx(ranked[1].score)
    assert ranked[0].score > ranked[2].score
    assert len(ranked) == 3
    assert [item.chunk.id for item in again] == [item.chunk.id for item in ranked]


def test_containing_keeps_the_numbered_chunk_even_when_its_score_is_lower() -> None:
    index = InMemoryVectorIndex(1)
    index.add(_chunk("teorema-871", "Teorema 8.7.1. Todo anillo conmutativo."), [0.95])
    index.add(_chunk("teorema-844", "Teorema 8.4.4. Sea G un grupo."), [0.4])
    index.add(
        _chunk("mencion", f"{'nota ' * 20}se recuerda el teorema 8.4.4."),
        [0.99],
    )

    unfiltered = index.search([1.0], top_k=1)
    filtered = index.search([1.0], top_k=2, containing=("8.4.4",))

    assert unfiltered[0].chunk.id == "mencion"
    assert [item.chunk.id for item in filtered] == ["teorema-844", "mencion"]
    assert filtered[0].score == pytest.approx(0.4)
    assert "8.7.1" not in filtered[0].chunk.text


def test_article_number_does_not_match_a_longer_number() -> None:
    index = InMemoryVectorIndex(1)
    index.add(_chunk("ciento-veinte", "El plazo es de 120 días."), [1.0])
    index.add(_chunk("articulo-doce", "Artículo 12. El plazo es de diez días."), [0.2])

    results = index.search([1.0], top_k=5, containing=("12",))

    assert [item.chunk.id for item in results] == ["articulo-doce"]


def test_spaced_dotted_number_matches_the_normalized_identifier() -> None:
    index = InMemoryVectorIndex(1)
    index.add(_chunk("espaciado", "Teorema 8. 4. 4. Sea G un grupo."), [0.3])

    results = index.search([1.0], top_k=1, containing=("8.4.4",))

    assert [item.chunk.id for item in results] == ["espaciado"]


def test_search_rejects_non_positive_top_k_and_empty_index() -> None:
    index = InMemoryVectorIndex(2)

    assert index.search([1.0, 0.0], top_k=3) == ()
    with pytest.raises(ValueError, match="top_k"):
        index.search([1.0, 0.0], top_k=0)


def test_stored_vector_is_not_aliased_to_the_caller_list() -> None:
    index = InMemoryVectorIndex(2)
    values = [1.0, 0.0]
    index.add(_chunk("solo"), values)
    values.append(9.0)

    result = index.search([1.0, 0.0], top_k=1)

    assert result[0].score == pytest.approx(1.0)
