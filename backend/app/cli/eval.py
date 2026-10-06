"""Comandos eval import, run, compare y gate."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from io import TextIOWrapper
from pathlib import Path
from uuid import UUID, uuid4

from app.config import get_settings
from app.documents.versions import CHUNKER_VERSION, PARSER_VERSION
from app.domain.enums import EvaluationRunStatus
from app.domain.errors import DomainError, EvaluationRejected
from app.domain.evaluation import EvaluationRun, config_fingerprint
from app.evaluation.context import context_for_evaluation
from app.evaluation.importing import corpus_sha256, load_cases
from app.evaluation.reference import ReferenceMode, reference_executor
from app.evaluation.reports import (
    comparison_payload,
    comparison_text,
    dumps,
    gate_decision,
    load_report,
    report_payload,
    report_text,
)
from app.evaluation.runner import PROFILE_REFERENCE, run_evaluation
from app.evaluation.store import persist_dataset, persist_run, tenant_id_for_slug
from app.generation.models import PROMPT_VERSION
from app.infrastructure.database import create_database_engine, create_session_factory

_USAGE = 2
_GATE_FAILED = 1


def main(argv: Sequence[str] | None = None) -> None:
    """Ejecuta un subcomando de evaluación y termina con su código."""
    _configure_stdio()
    parser = _parser()
    args = parser.parse_args(None if argv is None else list(argv))
    try:
        code = _dispatch(args)
    except (EvaluationRejected, DomainError) as error:
        print(error.message, file=sys.stderr)
        raise SystemExit(_USAGE) from error
    raise SystemExit(code)


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "import":
        return asyncio.run(_import(args))
    if args.command == "run":
        return asyncio.run(_run(args))
    if args.command == "compare":
        return _compare(args)
    if args.command == "gate":
        return _gate(args)
    return _USAGE


async def _import(args: argparse.Namespace) -> int:
    text, names = _read_inputs(args.cases, args.corpus)
    probe = uuid4()
    load_cases(
        text,
        names,
        version=args.version,
        tenant_id=probe,
        dataset_id=probe,
        new_id=uuid4,
    )
    engine = create_database_engine(get_settings().database_url)
    try:
        factory = create_session_factory(engine)
        tenant_id = await tenant_id_for_slug(factory, args.tenant_slug)
        dataset_id = await persist_dataset(
            factory,
            tenant_id=tenant_id,
            name=args.name,
            version=args.version,
            text=text,
            corpus_names=names,
            created_at=datetime.now(UTC),
            new_id=uuid4,
        )
    finally:
        await engine.dispose()
    print(dataset_id)
    return 0


async def _run(args: argparse.Namespace) -> int:
    if args.profile == "production":
        message = (
            "El perfil de producción no está promovido. "
            "Hace falta una corrida medida que pase eval gate."
        )
        print(message, file=sys.stderr)
        return _USAGE
    text, files = _read_corpus(args.cases, args.corpus)
    names = frozenset(files)
    tenant_id = _tenant_id(args.tenant_id)
    dataset_id = uuid4()
    cases = load_cases(
        text,
        names,
        version=args.version,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        new_id=uuid4,
    )
    mode = ReferenceMode(args.mode)
    report = await run_evaluation(
        cases,
        reference_executor(cases, mode=mode),
        context_for_evaluation(tenant_id),
        corpus_sha256=corpus_sha256(files),
        parser_version=PARSER_VERSION,
        chunker_version=CHUNKER_VERSION,
        prompt_version=PROMPT_VERSION,
        dataset_version=args.version,
        top_k=args.top_k,
        profile=PROFILE_REFERENCE,
        suggest_judge=args.suggest_judge,
    )
    _write(args.output, dumps(report_payload(report)))
    _write(args.markdown, report_text(report))
    if args.tenant_slug is not None and args.dataset_id is not None:
        await _store_report(args, report, tenant_id, UUID(args.dataset_id))
    return 0


def _compare(args: argparse.Namespace) -> int:
    baseline = load_report(Path(args.baseline).read_text(encoding="utf-8"))
    candidate = load_report(Path(args.candidate).read_text(encoding="utf-8"))
    decision = gate_decision(baseline, candidate, tolerance=args.tolerance)
    payload = comparison_payload(baseline, candidate, decision)
    _write(args.output, dumps(payload))
    _write(args.markdown, comparison_text(baseline, candidate, decision))
    return 0 if decision.passed else _GATE_FAILED


def _gate(args: argparse.Namespace) -> int:
    return _compare(args)


async def _store_report(
    args: argparse.Namespace,
    report: object,
    tenant_id: UUID,
    dataset_id: UUID,
) -> None:
    from app.evaluation.runner import EvaluationReport

    if not isinstance(report, EvaluationReport):
        message = "La corrida no es válida"
        raise EvaluationRejected(message)
    now = datetime.now(UTC)
    run = EvaluationRun(
        id=uuid4(),
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        version=report.run_version,
        status=EvaluationRunStatus.COMPLETED,
        config=report.config,
        metrics=report.metrics,
        failed_cases=report.failed_cases,
        isolation_ok=report.isolation_ok,
        created_at=now,
        updated_at=now,
    )
    if run.version != config_fingerprint(run.config):
        message = "La corrida no es válida"
        raise EvaluationRejected(message)
    engine = create_database_engine(get_settings().database_url)
    try:
        factory = create_session_factory(engine)
        stored_tenant = await tenant_id_for_slug(factory, args.tenant_slug)
        if stored_tenant != tenant_id:
            message = "La empresa no existe"
            raise EvaluationRejected(message)
        await persist_run(factory, run)
    finally:
        await engine.dispose()


def _read_inputs(cases_path: str, corpus_path: str) -> tuple[str, frozenset[str]]:
    text, files = _read_corpus(cases_path, corpus_path)
    return text, frozenset(files)


def _read_corpus(cases_path: str, corpus_path: str) -> tuple[str, dict[str, bytes]]:
    cases = Path(cases_path)
    corpus = Path(corpus_path)
    if not cases.is_file() or not corpus.is_dir():
        message = "El conjunto o el corpus no existen"
        raise EvaluationRejected(message)
    files = {
        path.name: path.read_bytes()
        for path in sorted(corpus.iterdir())
        if path.is_file() and not path.name.startswith(".")
    }
    if not files:
        message = "El corpus no tiene archivos"
        raise EvaluationRejected(message)
    return cases.read_text(encoding="utf-8"), files


def _tenant_id(raw: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as error:
        message = "El identificador de empresa no es válido"
        raise EvaluationRejected(message) from error


def _write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli.eval")
    sub = parser.add_subparsers(dest="command", required=True)
    _import_parser(sub)
    _run_parser(sub)
    _pair_parser(sub, "compare")
    _pair_parser(sub, "gate")
    return parser


def _import_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    command = sub.add_parser("import")
    command.add_argument("--cases", required=True)
    command.add_argument("--corpus", required=True)
    command.add_argument("--version", required=True)
    command.add_argument("--name", required=True)
    command.add_argument("--tenant-slug", required=True)


def _run_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    command = sub.add_parser("run")
    command.add_argument("--cases", required=True)
    command.add_argument("--corpus", required=True)
    command.add_argument("--version", required=True)
    command.add_argument("--tenant-id", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--markdown", required=True)
    command.add_argument(
        "--profile",
        choices=("deterministic", "production"),
        default="deterministic",
    )
    command.add_argument("--mode", choices=("reference", "drop_hits", "leak"), default="reference")
    command.add_argument("--top-k", type=int, default=5)
    command.add_argument("--suggest-judge", action="store_true")
    command.add_argument("--tenant-slug")
    command.add_argument("--dataset-id")


def _pair_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
) -> None:
    command = sub.add_parser(name)
    command.add_argument("--baseline", required=True)
    command.add_argument("--candidate", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--markdown", required=True)
    command.add_argument("--tolerance", type=float, default=0.02)


def _configure_stdio() -> None:
    if isinstance(sys.stdout, TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    if isinstance(sys.stderr, TextIOWrapper):
        sys.stderr.reconfigure(encoding="utf-8")


if __name__ == "__main__":
    main()
