"""El importador acepta el fixture y rechaza JSONL inválido."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.config import repository_root
from app.domain.errors import EvaluationRejected
from app.domain.evaluation import EvaluationCase, contains_secret
from app.evaluation.importing import load_cases

_SECRET = "api_key=super-secret-value"


def test_fixture_imports_and_covers_the_required_kinds() -> None:
    cases = _load_fixture()
    tags = {tag for case in cases for tag in case.tags}
    splits = {case.split.value for case in cases}

    assert len(cases) >= 30
    assert splits == {"calibration", "test"}
    assert tags >= {"parafrasis", "exacto", "numero", "ambigua", "sin_respuesta", "inyeccion"}
    assert all(case.labeled_answer_sha256 for case in cases if case.answerable)


def test_fixture_facts_come_from_the_corpus() -> None:
    root = repository_root() / "eval" / "fixtures" / "corpus"
    cases = _load_fixture()
    for case in cases:
        for fact in case.expected_facts:
            source = case.expected_sources[0]
            assert fact in (root / source).read_text(encoding="utf-8")


def test_fixture_has_no_secret_pattern() -> None:
    text = (repository_root() / "eval" / "fixtures" / "cases.jsonl").read_text(encoding="utf-8")

    assert not contains_secret(text)


def test_readme_blocks_the_production_profile() -> None:
    text = (repository_root() / "eval" / "README.md").read_text(encoding="utf-8")

    assert "sentence-transformers" in text
    assert "no se promueve" in text


def test_private_datasets_are_gitignored() -> None:
    ignored = (repository_root() / ".gitignore").read_text(encoding="utf-8")

    assert "/eval/private/" in ignored
    assert "/eval/reports/" in ignored


def test_rejects_malformed_jsonl() -> None:
    raw = _pair() + "\n{esto no es json}\n"

    with pytest.raises(EvaluationRejected, match="no es JSON válido"):
        _load(raw)


def test_rejects_duplicate_ids() -> None:
    line = _line("c-uno", "calibration")
    raw = f"{line}\n{line}\n{_line('t-uno', 'test')}\n{_line('t-dos', 'test')}\n"

    with pytest.raises(EvaluationRejected, match="está repetido"):
        _load(raw)


def test_rejects_a_missing_source() -> None:
    raw = _pair().replace("vacaciones.md", "no-existe.md")

    with pytest.raises(EvaluationRejected, match="no existe") as caught:
        _load(raw)

    assert "¿Cuántos" not in caught.value.message


def test_rejects_a_secret_without_echoing_it() -> None:
    raw = _pair().replace(
        "¿Cuántos días hábiles otorga?",
        f"¿Cuál es el valor {_SECRET}?",
    )

    with pytest.raises(EvaluationRejected, match="secreto") as caught:
        _load(raw)

    assert _SECRET not in caught.value.message


def test_rejects_a_dataset_without_both_splits() -> None:
    raw = "\n".join(_line(f"c-{index}", "calibration") for index in range(4))

    with pytest.raises(EvaluationRejected, match="splits"):
        _load(raw)


def _load_fixture() -> tuple[EvaluationCase, ...]:
    root = repository_root() / "eval" / "fixtures"
    text = (root / "cases.jsonl").read_text(encoding="utf-8")
    names = frozenset(path.name for path in (root / "corpus").iterdir() if path.is_file())
    return load_cases(
        text,
        names,
        version="lumin-v1",
        tenant_id=uuid4(),
        dataset_id=uuid4(),
        new_id=uuid4,
    )


def _load(raw: str) -> tuple[EvaluationCase, ...]:
    return load_cases(
        raw,
        frozenset({"vacaciones.md"}),
        version="v1",
        tenant_id=uuid4(),
        dataset_id=uuid4(),
        new_id=uuid4,
    )


def _pair() -> str:
    return "\n".join(
        (
            _line("c-uno", "calibration"),
            _line("c-dos", "calibration"),
            _line("t-uno", "test"),
            _line("t-dos", "test"),
        )
    )


def _line(case_id: str, split: str) -> str:
    return (
        '{"id":"'
        + case_id
        + '","question":"¿Cuántos días hábiles otorga?","answerable":true,'
        + '"expected_sources":["vacaciones.md"],"pages":[1],'
        + '"expected_facts":["18 días hábiles"],"expected_patterns":[],'
        + '"split":"'
        + split
        + '","tags":["numero"],"fidelity":"supported","label_version":1}'
    )
