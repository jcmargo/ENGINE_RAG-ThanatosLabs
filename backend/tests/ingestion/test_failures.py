"""Clasificación de fallos y arranque del worker sin cargar el modelo."""

import ast
import random
from pathlib import Path

from app.config import repository_root
from app.documents.parsers.base import (
    DocumentParseError,
    EmptyDocumentError,
    UnsupportedDocumentError,
)
from app.domain.errors import InvalidStateError
from app.ingestion.failures import (
    ContentHashMismatchError,
    MissingOriginalError,
    backoff_seconds,
    classify_failure,
)
from app.worker.__main__ import parse_command


def test_permanent_failures_keep_a_stable_code() -> None:
    cases = (
        (ContentHashMismatchError(), "content_hash_mismatch", True),
        (EmptyDocumentError("vacío"), "empty_document", True),
        (UnsupportedDocumentError("tipo"), "unsupported_document", True),
        (DocumentParseError("pdf"), "document_parse_error", True),
        (MissingOriginalError(), "missing_original", True),
        (InvalidStateError("ausente"), "missing_original", True),
        (TimeoutError(), "dependency_unavailable", False),
        (ConnectionError(), "dependency_unavailable", False),
        (OSError(), "dependency_unavailable", False),
        (RuntimeError("red"), "dependency_unavailable", False),
    )
    for error, code, permanent in cases:
        failure = classify_failure(error)
        assert failure.code == code
        assert failure.permanent is permanent


def test_backoff_uses_equal_jitter_and_caps() -> None:
    first = backoff_seconds(attempts=1, base_seconds=2, cap_seconds=300, rng=random.Random(1))
    assert 1 <= first <= 2
    later = backoff_seconds(attempts=20, base_seconds=2, cap_seconds=10, rng=random.Random(2))
    assert 5 <= later <= 10


def test_worker_entrypoint_defaults_to_run_and_accepts_reconcile() -> None:
    assert parse_command([]) == "run"
    assert parse_command(["reconcile"]) == "reconcile"


def test_worker_module_does_not_import_sentence_transformers() -> None:
    path = repository_root() / "backend" / "app" / "worker" / "runtime.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        modules = _imported_modules(node)
        assert "sentence_transformers" not in modules
        assert "app.embeddings.sentence_transformer" not in modules
        assert "app.embeddings.tokens" not in modules


def _imported_modules(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names}
    if isinstance(node, ast.ImportFrom):
        return {node.module or ""}
    return set()


def test_api_startup_source_does_not_open_the_worker_index() -> None:
    source = Path(repository_root() / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    assert "ensure_index" not in source
    assert "IngestionWorker" not in source
