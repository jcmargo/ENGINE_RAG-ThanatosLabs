"""Identificadores explícitos de una pregunta."""

import pytest

from app.retrieval.identifiers import (
    is_label_only,
    named_label,
    opens_with_kind,
    required_texts,
    statement_span,
)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("¿Cuál es el teorema 8.4.4?", ("8.4.4",)),
        ("¿Cuál es el teorema 8. 4. 4?", ("8.4.4",)),
        ("compara el 8.4.4 con el 8.7.1", ("8.4.4", "8.7.1")),
        ("¿Qué dice el artículo 12?", ("12",)),
        ("¿Qué dice el Artículo 12?", ("12",)),
        ("cláusula 3", ("3",)),
        ("paso 4", ("4",)),
        ("sección 2", ("2",)),
        ("¿Cuántos días de descanso anual tengo?", ()),
        ("¿Cuál es la capital de Japón?", ()),
        ("tengo 2 periodos", ()),
    ],
)
def test_required_texts(question: str, expected: tuple[str, ...]) -> None:
    assert required_texts(question) == expected


def test_statement_span_stops_before_the_proof() -> None:
    text = (
        "Notas del capítulo.\n\n"
        "Teorema 8.4.4 Sea G un grupo finito.\n\n"
        "Demostración\n"
        "El resto de la página no entra."
    )

    span = statement_span(text, ("8.4.4",))

    assert span.startswith("Teorema 8.4.4")
    assert "grupo finito" in span
    assert "no entra" not in span


def test_statement_span_stops_when_the_proof_opens_the_next_sentence() -> None:
    text = (
        "El teorema 8.4.4 describe el grupo generado. "
        "Demostración. El resto de la página trae 9991."
    )

    span = statement_span(text, ("8.4.4",))

    assert "grupo generado" in span
    assert "Demostración" not in span
    assert "9991" not in span


def test_named_label_keeps_one_kind_and_ignores_a_comparison() -> None:
    assert named_label("TEOREMA 7.4.4") == ("teorema", "7.4.4")
    assert named_label("¿Qué afirma el teorema 8.4.4?") == ("teorema", "8.4.4")
    assert named_label("compara el 8.4.4 con el 8.7.1") is None
    assert named_label("¿Cuántos días de descanso anual tengo?") is None


def test_an_example_heading_does_not_open_the_theorem() -> None:
    assert opens_with_kind("EJEMPLO 7.4.4", "teorema", "7.4.4") is False
    assert opens_with_kind("Teorema 7.4.4 Sea A una matriz.", "teorema", "7.4.4") is True


def test_label_only_is_the_heading_and_not_the_statement() -> None:
    assert is_label_only("7.4.4") is True
    assert is_label_only("EJEMPLO 7.4.4") is True
    assert is_label_only("18") is False
    assert is_label_only("Sea A una matriz de n por n.") is False


def test_statement_span_ends_on_a_sentence_when_the_page_continues() -> None:
    text = (
        "Teorema 8.4.4 "
        + ("Sea A una matriz. " * 47)
        + "Ahora empieza otra cosa que no pertenece al enunciado"
    )

    span = statement_span(text, ("8.4.4",))

    assert span.endswith(".")
    assert "Ahora empieza" not in span


def test_statement_span_keeps_a_proof_word_inside_the_statement() -> None:
    text = "Teorema 8.4.4 afirma que la demostración del lema anterior basta."

    span = statement_span(text, ("8.4.4",))

    assert span == text
