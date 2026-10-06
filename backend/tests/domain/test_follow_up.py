"""Unión de un seguimiento: recorte, vacío, parte pedida y tema reciente."""

from app.domain.follow_up import follow_up_query, plan_follow_up
from app.retrieval.identifiers import named_label

_CURRENT = "dime la demostracion"
_PREVIOUS = "que dice el teorema 8.4.4"


def test_a_follow_up_joins_the_previous_user_question() -> None:
    joined = follow_up_query(_CURRENT, _PREVIOUS, max_chars=2000)

    assert joined == f"{_PREVIOUS}\n{_CURRENT}"


def test_an_overlong_join_keeps_the_current_question_and_the_end_of_the_previous() -> None:
    current = "actual"
    previous = "0123456789ABCDEF"
    joined = follow_up_query(current, previous, max_chars=20)

    assert joined == f"{previous[-13:]}\n{current}"
    assert len(joined) == 20
    assert joined.endswith(current)
    assert previous[:3] not in joined


def test_a_blank_previous_question_leaves_the_current_one_untouched() -> None:
    assert follow_up_query(_CURRENT, None, max_chars=2000) == _CURRENT
    assert follow_up_query(_CURRENT, "", max_chars=2000) == _CURRENT
    assert follow_up_query(_CURRENT, " \n\t ", max_chars=2000) == _CURRENT


def test_the_same_question_is_not_repeated() -> None:
    assert follow_up_query(_CURRENT, _CURRENT, max_chars=2000) == _CURRENT
    assert follow_up_query(_CURRENT, "dime   la\ndemostracion", max_chars=2000) == _CURRENT


def test_a_current_question_at_the_limit_drops_the_previous_one() -> None:
    current = "x" * 20
    joined = follow_up_query(current, _PREVIOUS, max_chars=20)

    assert joined == current


def test_a_bare_part_uses_the_newest_label_and_keeps_the_older_one() -> None:
    plan = plan_follow_up(
        "demostración",
        ("definición", "TeoREma 8.4.4", "teorema 7.4.4"),
        max_chars=2000,
        topic_of=named_label,
    )

    assert plan.part == "demostracion"
    assert plan.topics == (("teorema", "8.4.4"), ("teorema", "7.4.4"))
    assert plan.query == "TeoREma 8.4.4\ndemostración"


def test_dime_la_demostracion_and_y_un_ejemplo_are_parts() -> None:
    demonstration = plan_follow_up(
        "dime la demostracion",
        (_PREVIOUS,),
        max_chars=2000,
        topic_of=named_label,
    )
    example = plan_follow_up(
        "y un ejemplo",
        ("dime la demostracion", _PREVIOUS),
        max_chars=2000,
        topic_of=named_label,
    )

    assert demonstration.part == "demostracion"
    assert demonstration.query == f"{_PREVIOUS}\ndime la demostracion"
    assert example.part == "ejemplo"
    assert example.topics == (("teorema", "8.4.4"),)
    assert example.query == f"{_PREVIOUS}\ny un ejemplo"


def test_a_part_that_names_its_topic_ignores_older_ones() -> None:
    plan = plan_follow_up(
        "demostracion del teorema 7.4.4",
        ("teorema 8.4.4",),
        max_chars=2000,
        topic_of=named_label,
    )

    assert plan.part == "demostracion"
    assert plan.topics == (("teorema", "7.4.4"),)
    assert plan.query == "demostracion del teorema 7.4.4"


def test_a_new_labeled_question_does_not_drag_the_previous_topic() -> None:
    plan = plan_follow_up(
        "teorema 7.4.4",
        ("teorema 8.4.4",),
        max_chars=2000,
        topic_of=named_label,
    )

    assert plan.part is None
    assert plan.topics == ()
    assert plan.query == "teorema 7.4.4"


def test_a_plain_question_still_joins_only_the_previous_one() -> None:
    plan = plan_follow_up(
        "¿qué hipótesis usa?",
        ("dime la demostracion", _PREVIOUS),
        max_chars=2000,
        topic_of=named_label,
    )

    assert plan.part is None
    assert plan.query == "dime la demostracion\n¿qué hipótesis usa?"
    assert "8.4.4" not in plan.query


def test_labels_older_than_the_last_six_questions_are_dropped() -> None:
    recent = (*(f"nota {index}" for index in range(6)), _PREVIOUS)
    plan = plan_follow_up("ejemplo", recent, max_chars=2000, topic_of=named_label)

    assert plan.topics == ()
    assert plan.query == "ejemplo"
