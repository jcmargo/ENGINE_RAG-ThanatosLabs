"""Importa JSONL. Rechaza secretos, ids repetidos y fuentes que no existen."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

from app.domain.enums import (
    EvaluationCaseStatus,
    EvaluationDatasetStatus,
    EvaluationSplit,
    FidelityLabel,
)
from app.domain.errors import EvaluationRejected
from app.domain.evaluation import (
    EvaluationCase,
    EvaluationDataset,
    answer_sha256,
    contains_secret,
    require_split_balance,
)

logger = logging.getLogger(__name__)

_CASE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_TAG = re.compile(r"^[a-z0-9_]{1,32}$")
_SOURCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SCHEMA = "El caso en la línea {line} no cumple el esquema"
_JSON = "La línea {line} no es JSON válido"
_SECRET = "La línea {line} contiene un secreto y no se importa"


class CaseLine(BaseModel):
    """Línea JSONL ya separada del archivo. No acepta campos desconocidos."""

    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    answerable: bool
    expected_sources: list[str] = []
    pages: list[int] | None = None
    expected_facts: list[str] = []
    expected_patterns: list[str] = []
    split: Literal["calibration", "test"]
    tags: list[str]
    fidelity: Literal["supported", "partially_supported", "unsupported"] | None = None
    label_version: int | None = None


def content_sha256(text: str) -> str:
    """Huella del JSONL importado."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def corpus_sha256(files: Mapping[str, bytes]) -> str:
    """Huella del corpus por nombre de archivo, sin guardar el texto."""
    hasher = hashlib.sha256()
    for name in sorted(files):
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(files[name])
        hasher.update(b"\0")
    return hasher.hexdigest()


def load_cases(
    text: str,
    corpus_names: frozenset[str],
    *,
    version: str,
    tenant_id: UUID,
    dataset_id: UUID,
    new_id: Callable[[], UUID],
) -> tuple[EvaluationCase, ...]:
    """Valida el JSONL y devuelve casos de dominio. No registra la pregunta."""
    cases = _parse(text, corpus_names, version=version)
    built = tuple(
        _to_case(
            line,
            version=version,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            case_id=new_id(),
        )
        for line in cases
    )
    logger.info("evaluacion import cases=%d version=%s", len(built), version)
    return built


def build_dataset(
    *,
    dataset_id: UUID,
    tenant_id: UUID,
    name: str,
    version: str,
    text: str,
    cases: tuple[EvaluationCase, ...],
    created_at: datetime,
) -> EvaluationDataset:
    """Arma el conjunto activo. `created_at` lo pone el llamador."""
    return EvaluationDataset(
        id=dataset_id,
        tenant_id=tenant_id,
        name=name,
        version=version,
        status=EvaluationDatasetStatus.ACTIVE,
        content_sha256=content_sha256(text),
        case_count=len(cases),
        created_at=created_at,
        updated_at=created_at,
    )


def _parse(text: str, corpus_names: frozenset[str], *, version: str) -> tuple[CaseLine, ...]:
    if not version.strip():
        message = "La versión del conjunto no es válida"
        raise EvaluationRejected(message)
    lines = _lines(text)
    parsed: list[CaseLine] = []
    seen: set[str] = set()
    for line_number, raw in lines:
        if contains_secret(raw):
            raise EvaluationRejected(_SECRET.format(line=line_number))
        loaded = _load_json(raw, line_number)
        case = _validate_line(loaded, line_number)
        _reject_semantics(case, corpus_names, line_number)
        if case.id in seen:
            message = f"El caso {case.id} está repetido"
            raise EvaluationRejected(message)
        seen.add(case.id)
        parsed.append(case)
    counts: Counter[EvaluationSplit] = Counter(EvaluationSplit(item.split) for item in parsed)
    require_split_balance(counts)
    return tuple(parsed)


def _lines(text: str) -> tuple[tuple[int, str], ...]:
    rows: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if stripped:
            rows.append((number, stripped))
    if not rows:
        message = "El conjunto no tiene casos"
        raise EvaluationRejected(message)
    return tuple(rows)


def _load_json(raw: str, line_number: int) -> object:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise EvaluationRejected(_JSON.format(line=line_number)) from error


def _validate_line(loaded: object, line_number: int) -> CaseLine:
    try:
        return CaseLine.model_validate(loaded)
    except ValidationError as error:
        raise EvaluationRejected(_SCHEMA.format(line=line_number)) from error


def _reject_semantics(case: CaseLine, corpus_names: frozenset[str], line_number: int) -> None:
    if _CASE_ID.fullmatch(case.id) is None or not case.question.strip():
        raise EvaluationRejected(_SCHEMA.format(line=line_number))
    if len(case.question.strip()) > 2000 or not case.tags:
        raise EvaluationRejected(_SCHEMA.format(line=line_number))
    if any(_TAG.fullmatch(tag) is None for tag in case.tags):
        raise EvaluationRejected(_SCHEMA.format(line=line_number))
    if case.answerable and not case.expected_sources:
        message = f"El caso {case.id} no indica fuentes"
        raise EvaluationRejected(message)
    if case.answerable and not case.expected_facts and not case.expected_patterns:
        message = f"El caso {case.id} no indica hechos ni patrones"
        raise EvaluationRejected(message)
    for source in case.expected_sources:
        if _SOURCE.fullmatch(source) is None:
            raise EvaluationRejected(_SCHEMA.format(line=line_number))
        if source not in corpus_names:
            message = f"La fuente {source} no existe"
            raise EvaluationRejected(message)
    if case.pages is not None and not _valid_pages(case.pages):
        raise EvaluationRejected(_SCHEMA.format(line=line_number))
    for pattern in case.expected_patterns:
        try:
            re.compile(pattern)
        except re.error as error:
            raise EvaluationRejected(_SCHEMA.format(line=line_number)) from error
    if case.fidelity is not None:
        if not case.answerable or len(case.expected_facts) != 1 or case.label_version is None:
            message = f"El caso {case.id} no versiona la etiqueta humana"
            raise EvaluationRejected(message)
        if case.label_version < 1:
            message = f"El caso {case.id} no versiona la etiqueta humana"
            raise EvaluationRejected(message)
    elif case.label_version is not None and case.label_version < 1:
        raise EvaluationRejected(_SCHEMA.format(line=line_number))


def _valid_pages(pages: list[int]) -> bool:
    if not pages:
        return False
    return all(not isinstance(page, bool) and isinstance(page, int) and page >= 1 for page in pages)


def _to_case(
    line: CaseLine,
    *,
    version: str,
    tenant_id: UUID,
    dataset_id: UUID,
    case_id: UUID,
) -> EvaluationCase:
    fidelity = None if line.fidelity is None else FidelityLabel(line.fidelity)
    labeled = None if fidelity is None else answer_sha256(line.expected_facts[0])
    pages = () if line.pages is None else tuple(line.pages)
    return EvaluationCase(
        id=case_id,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        case_key=line.id,
        version=version,
        status=EvaluationCaseStatus.ACTIVE,
        split=EvaluationSplit(line.split),
        question=line.question.strip(),
        answerable=line.answerable,
        expected_sources=tuple(line.expected_sources),
        expected_pages=pages,
        expected_facts=tuple(line.expected_facts),
        expected_patterns=tuple(line.expected_patterns),
        tags=tuple(line.tags),
        fidelity_label=fidelity,
        label_version=1 if line.label_version is None else line.label_version,
        labeled_answer_sha256=labeled,
    )
