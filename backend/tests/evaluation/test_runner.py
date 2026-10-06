"""Corrida determinista, reportes y códigos de salida del gate."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.cli.eval import main
from app.config import repository_root
from app.documents.versions import CHUNKER_VERSION, PARSER_VERSION
from app.domain.evaluation import (
    MAX_UNSUPPORTED_RATE,
    MIN_ABSTENTION_RATE,
    MIN_CITATION_RATE,
    MIN_HIT_RATE,
)
from app.evaluation.context import context_for_evaluation
from app.evaluation.importing import corpus_sha256, load_cases
from app.evaluation.reference import ReferenceMode, reference_executor
from app.evaluation.reports import dumps, load_report, report_payload, report_text
from app.evaluation.runner import EvaluationReport, run_evaluation
from app.generation.models import PROMPT_VERSION

_TENANT = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_DATASET = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
_FACT = "La cooperativa otorga 18 días hábiles de vacaciones al año."
_MARKER = "MARCA-AISLAMIENTO-NO-REPORTAR"


def test_reference_run_is_deterministic_and_meets_the_gates() -> None:
    first = asyncio.run(_run())
    second = asyncio.run(_run(suggest_judge=True))
    payload = report_payload(first)
    rendered = report_text(first) + dumps(payload)

    assert payload == report_payload(second)
    assert first.failed_cases == ()
    assert first.isolation_ok is True
    assert first.metrics.hit_at_5.value is not None
    assert first.metrics.hit_at_5.value >= MIN_HIT_RATE
    assert first.metrics.citation.value is not None
    assert first.metrics.citation.value >= MIN_CITATION_RATE
    assert first.metrics.unanswerable_abstention.value is not None
    assert first.metrics.unanswerable_abstention.value >= MIN_ABSTENTION_RATE
    assert first.metrics.fidelity_unsupported.value == 0
    assert first.metrics.fidelity_unsupported.value < MAX_UNSUPPORTED_RATE
    assert first.metrics.hit_at_5.numerator == first.metrics.hit_at_5.denominator
    assert "deterministic-fake-v1" in rendered
    assert first.config.parser_version == PARSER_VERSION
    assert _FACT not in rendered
    assert _MARKER not in rendered


def test_runner_logs_do_not_include_the_question_or_the_document(
    caplog: pytest.LogCaptureFixture,
) -> None:
    question = "¿De cuántos días hábiles dispone una persona al año para vacaciones?"
    with caplog.at_level(logging.INFO):
        asyncio.run(_run())

    assert question not in caplog.text
    assert _FACT not in caplog.text
    assert _MARKER not in caplog.text


def test_gate_accepts_the_baseline_and_rejects_a_regression(tmp_path: Path) -> None:
    baseline = asyncio.run(_run())
    regression = asyncio.run(_run(mode=ReferenceMode.DROP_HITS))
    leak = asyncio.run(_run(mode=ReferenceMode.LEAK))
    base_json = tmp_path / "baseline.json"
    base_md = tmp_path / "baseline.md"
    drop_json = tmp_path / "drop.json"
    drop_md = tmp_path / "drop.md"
    leak_json = tmp_path / "leak.json"
    gate_json = tmp_path / "gate.json"
    gate_md = tmp_path / "gate.md"
    base_json.write_text(dumps(report_payload(baseline)), encoding="utf-8")
    base_md.write_text(report_text(baseline), encoding="utf-8")
    drop_json.write_text(dumps(report_payload(regression)), encoding="utf-8")
    drop_md.write_text(report_text(regression), encoding="utf-8")
    leak_json.write_text(dumps(report_payload(leak)), encoding="utf-8")

    assert regression.failed_cases
    assert "t-para-vacaciones" in drop_md.read_text(encoding="utf-8")
    assert PARSER_VERSION in drop_md.read_text(encoding="utf-8")
    assert CHUNKER_VERSION in drop_md.read_text(encoding="utf-8")
    assert _FACT not in drop_md.read_text(encoding="utf-8")
    assert leak.isolation_ok is False
    assert _MARKER not in leak_json.read_text(encoding="utf-8")

    with pytest.raises(SystemExit) as approved:
        main(
            [
                "gate",
                "--baseline",
                str(base_json),
                "--candidate",
                str(base_json),
                "--output",
                str(gate_json),
                "--markdown",
                str(gate_md),
            ]
        )
    assert approved.value.code == 0

    with pytest.raises(SystemExit) as rejected:
        main(
            [
                "gate",
                "--baseline",
                str(base_json),
                "--candidate",
                str(drop_json),
                "--output",
                str(tmp_path / "rejected.json"),
                "--markdown",
                str(tmp_path / "rejected.md"),
            ]
        )
    assert rejected.value.code == 1
    rejected_text = (tmp_path / "rejected.md").read_text(encoding="utf-8")
    assert "t-para-vacaciones" in rejected_text
    assert PROMPT_VERSION in rejected_text
    assert _FACT not in rejected_text

    with pytest.raises(SystemExit) as leaked:
        main(
            [
                "compare",
                "--baseline",
                str(base_json),
                "--candidate",
                str(leak_json),
                "--output",
                str(tmp_path / "leak-compare.json"),
                "--markdown",
                str(tmp_path / "leak-compare.md"),
            ]
        )
    assert leaked.value.code == 1


def test_production_profile_is_not_promoted(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "run",
                "--profile",
                "production",
                "--cases",
                str(tmp_path / "missing.jsonl"),
                "--corpus",
                str(tmp_path),
                "--version",
                "v1",
                "--tenant-id",
                "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "--output",
                str(tmp_path / "out.json"),
                "--markdown",
                str(tmp_path / "out.md"),
            ]
        )

    assert caught.value.code == 2


def test_import_command_rejects_malformed_json_without_a_database(tmp_path: Path) -> None:
    cases = tmp_path / "cases.jsonl"
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "vacaciones.md").write_text("18 días hábiles\n", encoding="utf-8")
    cases.write_text("{no es json}\n", encoding="utf-8")

    with pytest.raises(SystemExit) as caught:
        main(
            [
                "import",
                "--cases",
                str(cases),
                "--corpus",
                str(corpus),
                "--version",
                "v1",
                "--name",
                "lumen",
                "--tenant-slug",
                "lumen",
            ]
        )

    assert caught.value.code == 2


def test_eval_module_exits_2_for_the_production_profile() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.cli.eval",
            "run",
            "--cases",
            "missing.jsonl",
            "--corpus",
            "missing",
            "--version",
            "lumin-v1",
            "--tenant-id",
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "--profile",
            "production",
            "--output",
            "out.json",
            "--markdown",
            "out.md",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(repository_root() / "backend")},
    )

    assert completed.returncode == 2
    assert "eval gate" in completed.stderr


def test_loaded_report_roundtrip_keeps_counts() -> None:
    report = asyncio.run(_run())
    restored = load_report(dumps(report_payload(report)))

    assert restored.run_version == report.run_version
    assert restored.metrics.hit_at_5 == report.metrics.hit_at_5
    assert restored.config.embedding_version == report.config.embedding_version


async def _run(
    *,
    mode: ReferenceMode = ReferenceMode.REFERENCE,
    suggest_judge: bool = False,
) -> EvaluationReport:
    root = repository_root() / "eval" / "fixtures"
    text = (root / "cases.jsonl").read_text(encoding="utf-8")
    files = {path.name: path.read_bytes() for path in (root / "corpus").iterdir() if path.is_file()}
    cases = load_cases(
        text,
        frozenset(files),
        version="lumin-v1",
        tenant_id=_TENANT,
        dataset_id=_DATASET,
        new_id=uuid4,
    )
    return await run_evaluation(
        cases,
        reference_executor(cases, mode=mode),
        context_for_evaluation(_TENANT),
        corpus_sha256=corpus_sha256(files),
        parser_version=PARSER_VERSION,
        chunker_version=CHUNKER_VERSION,
        prompt_version=PROMPT_VERSION,
        dataset_version="lumin-v1",
        top_k=5,
        suggest_judge=suggest_judge,
    )
