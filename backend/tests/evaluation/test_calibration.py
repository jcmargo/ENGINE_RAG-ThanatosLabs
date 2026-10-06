"""La calibración no mira los casos marcados como test."""

from app.domain.enums import EvaluationSplit
from app.domain.evaluation import (
    CalibrationHit,
    CalibrationObservation,
    SplitObservation,
    calibrate_min_retrieval_score,
    calibration_observations,
    freeze_threshold,
)


def test_threshold_uses_only_the_calibration_split() -> None:
    calibration = (
        _item("c-respuesta", EvaluationSplit.CALIBRATION, answerable=True, score=0.95),
        _item("c-abstencion", EvaluationSplit.CALIBRATION, answerable=False, score=0.20),
    )
    leaked = _item("t-extremo", EvaluationSplit.TEST, answerable=True, score=0.30)

    without_test = freeze_threshold(calibration)
    with_test = freeze_threshold((*calibration, leaked))
    selected = calibration_observations((*calibration, leaked))

    assert without_test == with_test == 0.95
    assert tuple(item.case_id for item in selected) == ("c-respuesta", "c-abstencion")
    assert calibrate_min_retrieval_score(tuple(item.observation for item in calibration)) == 0.95


def test_a_test_case_would_have_changed_the_threshold() -> None:
    mixed = (
        CalibrationObservation(
            case_id="c-respuesta",
            answerable=True,
            expected_sources=("a.md",),
            hits=(CalibrationHit(source="a.md", score=0.95, page=1),),
        ),
        CalibrationObservation(
            case_id="c-abstencion",
            answerable=False,
            expected_sources=(),
            hits=(CalibrationHit(source="b.md", score=0.20, page=1),),
        ),
        CalibrationObservation(
            case_id="t-extremo",
            answerable=True,
            expected_sources=("c.md",),
            hits=(CalibrationHit(source="c.md", score=0.30, page=1),),
        ),
    )

    assert calibrate_min_retrieval_score(mixed) == 0.30


def _item(
    case_id: str,
    split: EvaluationSplit,
    *,
    answerable: bool,
    score: float,
) -> SplitObservation:
    source = "a.md" if answerable else "b.md"
    return SplitObservation(
        split=split,
        observation=CalibrationObservation(
            case_id=case_id,
            answerable=answerable,
            expected_sources=("a.md",) if answerable else (),
            hits=(CalibrationHit(source=source, score=score, page=1),),
        ),
    )
