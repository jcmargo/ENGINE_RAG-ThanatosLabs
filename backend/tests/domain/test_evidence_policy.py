"""Piso semántico y piso lexical, sin embeddings."""

import pytest

from app.domain.evidence_policy import EvidenceCandidate, select_evidence

_SEMANTIC = 0.857
_LEXICAL = 0.80

_VACATION = (
    "ThanatosLabs otorga 18 días hábiles de vacaciones al año como descanso anual "
    "de cada empleado a tiempo completo.\n\n"
    "La solicitud se envía al responsable con antelación.\n\n"
    "El disfrute puede ser consecutivo o dividirse en dos periodos."
)
_EXPENSES = (
    "El máximo reembolsable de taxi es de 40 USD por trayecto.\n\n"
    "Por encima de ese importe, el responsable aprueba el gasto por escrito "
    "antes del reembolso.\n\n"
    "Los comprobantes se conservan durante el ejercicio fiscal."
)
_SUPPORT = (
    "Los incidentes críticos son atendidos en 15 minutos.\n\n"
    "El equipo de soporte registra el número de ticket en la herramienta interna.\n\n"
    "Las incidencias ordinarias siguen la cola del servicio."
)


def _take(
    question: str, candidates: tuple[EvidenceCandidate, ...]
) -> tuple[EvidenceCandidate, ...]:
    return select_evidence(
        question,
        candidates,
        semantic_floor=_SEMANTIC,
        lexical_floor=_LEXICAL,
    )


def _choose(question: str, candidates: tuple[EvidenceCandidate, ...]) -> tuple[str, ...]:
    return tuple(item.source for item in _take(question, candidates))


def test_semantic_floor_accepts_without_shared_words() -> None:
    candidate = EvidenceCandidate("politica-vacaciones.md", 0.866878, _VACATION)
    chosen = select_evidence(
        "¿Cómo se calcula la derivada de x al cuadrado?",
        (candidate,),
        semantic_floor=_SEMANTIC,
        lexical_floor=_LEXICAL,
    )

    assert chosen == (candidate,)


def test_measured_paraphrases_pass_on_the_lexical_floor() -> None:
    cases = (
        (
            "¿Se pueden dividir las vacaciones en dos periodos?",
            EvidenceCandidate("politica-vacaciones.md", 0.834116, _VACATION),
        ),
        (
            "¿Con qué antelación se envía la solicitud de vacaciones?",
            EvidenceCandidate("politica-vacaciones.md", 0.811578, _VACATION),
        ),
        (
            "¿Durante cuánto tiempo se conservan los comprobantes?",
            EvidenceCandidate("politica-gastos.md", 0.800809, _EXPENSES),
        ),
        (
            "¿Cuál es el plazo de atención de los incidentes críticos?",
            EvidenceCandidate("procedimiento-soporte.md", 0.853719, _SUPPORT),
        ),
    )

    for question, candidate in cases:
        assert _choose(question, (candidate,)) == (candidate.source,)


def test_distractor_without_the_word_stays_out() -> None:
    chosen = _choose(
        "¿Cómo se calcula la derivada de x al cuadrado?",
        (EvidenceCandidate("politica-vacaciones.md", 0.82, _VACATION),),
    )

    assert chosen == ()


def test_word_below_the_lexical_floor_stays_out() -> None:
    chosen = _choose(
        "¿Se pueden dividir las vacaciones en dos periodos?",
        (EvidenceCandidate("politica-vacaciones.md", 0.799, _VACATION),),
    )

    assert chosen == ()


def test_close_scores_keep_the_file_with_more_question_words() -> None:
    chosen = _choose(
        "¿Durante cuánto tiempo se conservan los comprobantes?",
        (
            EvidenceCandidate(
                "procedimiento-soporte.md",
                0.8005,
                "Los comprobantes se conservan en la cola.",
            ),
            EvidenceCandidate(
                "politica-gastos.md",
                0.801,
                "Los comprobantes se conservan durante el tiempo del ejercicio.",
            ),
        ),
    )

    assert chosen == ("politica-gastos.md",)


def test_close_scores_with_the_same_word_count_abstain() -> None:
    chosen = _choose(
        "¿Durante cuánto tiempo se conservan los comprobantes?",
        (
            EvidenceCandidate("procedimiento-soporte.md", 0.800, "Los comprobantes se conservan."),
            EvidenceCandidate("politica-gastos.md", 0.801, "Se conservan los comprobantes."),
        ),
    )

    assert chosen == ()


def test_a_clear_score_gap_keeps_the_higher_file() -> None:
    chosen = _choose(
        "¿Durante cuánto tiempo se conservan los comprobantes?",
        (
            EvidenceCandidate(
                "procedimiento-soporte.md",
                0.84,
                "Los comprobantes se conservan aparte.",
            ),
            EvidenceCandidate(
                "politica-gastos.md",
                0.81,
                "Los comprobantes se conservan durante el tiempo del ejercicio.",
            ),
        ),
    )

    assert chosen == ("procedimiento-soporte.md",)


def test_semantic_hits_are_not_mixed_with_another_file() -> None:
    chosen = _choose(
        "¿Se pueden dividir las vacaciones en dos periodos?",
        (
            EvidenceCandidate("politica-vacaciones.md", 0.90, "Texto sin la palabra."),
            EvidenceCandidate("politica-gastos.md", 0.85, _VACATION),
        ),
    )

    assert chosen == ("politica-vacaciones.md",)


def test_a_single_shared_word_does_not_admit_an_unrelated_question() -> None:
    telework = _choose(
        "¿Puedo hacer teletrabajo tres días a la semana?",
        (EvidenceCandidate("politica-vacaciones.md", 0.84, _VACATION),),
    )
    parenthood = _choose(
        "¿Cuántos días de licencia por paternidad otorga la empresa?",
        (EvidenceCandidate("politica-vacaciones.md", 0.84, _VACATION),),
    )

    assert telework == ()
    assert parenthood == ()


def test_word_boundary_does_not_match_inside_a_longer_token() -> None:
    chosen = _choose(
        "¿Cuál es el marco aplicable?",
        (EvidenceCandidate("notas.md", 0.83, "Un submarco de referencia."),),
    )

    assert chosen == ()


def test_rejects_a_floor_outside_the_unit_interval() -> None:
    with pytest.raises(ValueError, match="semantic_floor"):
        select_evidence(
            "pregunta",
            (),
            semantic_floor=1.1,
            lexical_floor=_LEXICAL,
        )


def test_singular_matches_a_plural_token_with_s_or_es() -> None:
    hours = _choose(
        "¿A qué hora?",
        (EvidenceCandidate("horario.md", 0.83, "La oficina abre en horas de la mañana."),),
    )
    accounts = _choose(
        "¿En qué cuenta?",
        (EvidenceCandidate("banco.md", 0.83, "El pago entra en las cuentas de la empresa."),),
    )

    assert hours == ("horario.md",)
    assert accounts == ("banco.md",)


def test_dividir_does_not_match_dividirse() -> None:
    chosen = _choose(
        "¿Se debe dividir la tarea urgente?",
        (EvidenceCandidate("notas.md", 0.83, "La tarea puede dividirse."),),
    )

    assert chosen == ()


@pytest.mark.parametrize("score", (0.84, 0.91))
def test_ordinary_incidents_keep_the_queue_sentence(score: float) -> None:
    chosen = _take(
        "¿Cómo se gestionan las incidencias ordinarias?",
        (EvidenceCandidate("procedimiento-soporte.md", score, _SUPPORT),),
    )

    assert len(chosen) == 1
    assert chosen[0].source == "procedimiento-soporte.md"
    assert "cola del servicio" in chosen[0].text
    assert "15" not in chosen[0].text


@pytest.mark.parametrize("score", (0.84, 0.91))
def test_ordinary_incident_minutes_have_no_evidence(score: float) -> None:
    chosen = _choose(
        "¿En cuántos minutos se atienden las incidencias ordinarias?",
        (EvidenceCandidate("procedimiento-soporte.md", score, _SUPPORT),),
    )

    assert chosen == ()


@pytest.mark.parametrize("score", (0.853719, 0.91))
def test_critical_incidents_keep_the_minutes_sentence(score: float) -> None:
    chosen = _take(
        "¿Cuál es el plazo de atención de los incidentes críticos?",
        (EvidenceCandidate("procedimiento-soporte.md", score, _SUPPORT),),
    )

    assert len(chosen) == 1
    assert "15 minutos" in chosen[0].text
    assert "ordinarias" not in chosen[0].text


@pytest.mark.parametrize("score", (0.84, 0.91))
def test_plane_reimbursement_has_no_evidence(score: float) -> None:
    chosen = _choose(
        "¿Cuál es el máximo reembolsable de un avión?",
        (EvidenceCandidate("politica-gastos.md", score, _EXPENSES),),
    )

    assert chosen == ()


@pytest.mark.parametrize("score", (0.84, 0.91))
def test_known_taxi_question_keeps_the_fare_sentence(score: float) -> None:
    chosen = _take(
        "¿Cuál es el máximo reembolsable de taxi?",
        (EvidenceCandidate("politica-gastos.md", score, _EXPENSES),),
    )

    assert len(chosen) == 1
    assert "40" in chosen[0].text
    assert "taxi" in chosen[0].text
    assert "comprobantes" not in chosen[0].text


def test_taxi_cap_is_not_dropped_as_a_bare_measure() -> None:
    chosen = _take(
        "¿Cuál es el tope de taxi por trayecto?",
        (EvidenceCandidate("politica-gastos.md", 0.84, _EXPENSES),),
    )

    assert len(chosen) == 1
    assert "40" in chosen[0].text
    assert "taxi" in chosen[0].text


def test_receipt_retention_keeps_the_fiscal_year_sentence() -> None:
    chosen = _take(
        "¿Durante cuánto tiempo se conservan los comprobantes?",
        (EvidenceCandidate("politica-gastos.md", 0.800809, _EXPENSES),),
    )

    assert len(chosen) == 1
    assert "ejercicio fiscal" in chosen[0].text
    assert "40" not in chosen[0].text


def test_vacation_periods_keep_both_tied_sentences() -> None:
    chosen = _take(
        "¿Se pueden dividir las vacaciones en dos periodos?",
        (EvidenceCandidate("politica-vacaciones.md", 0.834116, _VACATION),),
    )

    assert len(chosen) == 1
    assert "periodos" in chosen[0].text
    assert "vacaciones" in chosen[0].text
    assert "antelación" not in chosen[0].text


def test_disjoint_lexical_tie_abstains_even_with_a_clear_score_gap() -> None:
    chosen = _choose(
        "¿Cuál es la contraseña de acceso de la herramienta interna?",
        (
            EvidenceCandidate("notas.md", 0.85, "La contraseña de acceso caduca."),
            EvidenceCandidate(
                "procedimiento-soporte.md",
                0.81,
                "El equipo usa la herramienta interna.",
            ),
        ),
    )

    assert chosen == ()


def test_split_question_words_do_not_let_vacations_beat_the_equipment_file() -> None:
    chosen = _choose(
        "¿Puede llevar el portátil en vacaciones?",
        (
            EvidenceCandidate(
                "politica-vacaciones.md",
                0.830,
                "Puede pedir vacaciones.\n\nLlevar el formulario es aparte.",
            ),
            EvidenceCandidate("politica-equipo.md", 0.835, "Puede llevar el portátil."),
        ),
    )

    assert chosen == ("politica-equipo.md",)


def test_decimal_identifier_stays_inside_one_sentence() -> None:
    chosen = _take(
        "¿Qué afirma el teorema 8.4.4?",
        (
            EvidenceCandidate(
                "algebra.pdf",
                0.84,
                "El teorema 8.4.4 describe el grupo. Otra frase habla de matrices.",
            ),
        ),
    )

    assert len(chosen) == 1
    assert "8.4.4" in chosen[0].text
    assert "matrices" not in chosen[0].text
