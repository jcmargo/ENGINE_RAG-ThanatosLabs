"""Identificadores explícitos de una pregunta."""

import pytest

from app.retrieval.identifiers import required_texts, statement_span


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
