"""Interpreta el JSON del modelo y descarta citas que no fueron recuperadas."""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.generation.errors import GenerationFormatError, UngroundedAnswerError
from app.generation.models import PUBLIC_ABSTENTION, Citation
from app.generation.prompt import source_label
from app.retrieval.sample_search import LocalSearchHit


@dataclass(frozen=True)
class ModelOutput:
    """Campos pedidos al modelo, antes de construir las citas."""

    answer: str
    abstained: bool
    cited_chunk_ids: tuple[str, ...]


def parse_model_output(text: str) -> ModelOutput:
    """Acepta el objeto JSON pedido. No incorpora el texto crudo al error."""
    payload = _load_object(text)
    if not isinstance(payload, dict):
        message = "La salida del modelo no es JSON utilizable"
        raise GenerationFormatError(message)
    answer = payload.get("answer")
    abstained = payload.get("abstained")
    cited = payload.get("cited_chunk_ids")
    if (
        not isinstance(answer, str)
        or not isinstance(abstained, bool)
        or not isinstance(cited, list)
        or any(not isinstance(item, str) or not item.strip() for item in cited)
    ):
        message = "La salida del modelo no tiene el esquema esperado"
        raise GenerationFormatError(message)
    if not abstained and not answer.strip():
        message = "La salida del modelo no tiene el esquema esperado"
        raise GenerationFormatError(message)
    return ModelOutput(
        answer=answer.strip(),
        abstained=abstained,
        cited_chunk_ids=tuple(item.strip() for item in cited),
    )


def ground_output(
    output: ModelOutput,
    evidence: Sequence[LocalSearchHit],
) -> tuple[str, bool, tuple[Citation, ...]]:
    """Quita IDs desconocidos. Sin una cita válida, la respuesta no se acepta."""
    by_label = {source_label(index): hit for index, hit in enumerate(evidence, start=1)}
    citations: list[Citation] = []
    cited_hits: list[LocalSearchHit] = []
    seen: set[str] = set()
    for label in output.cited_chunk_ids:
        if label in seen or label not in by_label:
            continue
        seen.add(label)
        hit = by_label[label]
        citations.append(_citation(hit))
        cited_hits.append(hit)
    if output.abstained:
        return PUBLIC_ABSTENTION, True, ()
    if not citations:
        message = "La respuesta no se acepta porque no cita un fragmento recuperado"
        raise UngroundedAnswerError(message)
    if not _cited_numbers_are_supported(output.answer, cited_hits):
        return PUBLIC_ABSTENTION, True, ()
    return output.answer, False, tuple(citations)


def _cited_numbers_are_supported(answer: str, hits: Sequence[LocalSearchHit]) -> bool:
    """Una cifra citada tiene que aparecer en el fragmento, no inventarse."""
    haystack = "\n".join(hit.result.chunk.text for hit in hits)
    return all(
        re.search(rf"(?<!\d){re.escape(number)}(?!\d)", haystack) is not None
        for number in re.findall(r"\d+", answer)
    )


def _citation(hit: LocalSearchHit) -> Citation:
    chunk = hit.result.chunk
    return Citation(
        chunk_id=chunk.id,
        source=hit.source,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        section=chunk.section,
        excerpt=_excerpt(chunk.text),
    )


def _excerpt(text: str, limit: int = 400) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3].rstrip() + "..."


def _load_object(text: str) -> object:
    candidate = _strip_fence(text.strip())
    loaded = _loads(candidate)
    if loaded is not None:
        return loaded
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end <= start:
        message = "La salida del modelo no es JSON utilizable"
        raise GenerationFormatError(message)
    recovered = _loads(candidate[start : end + 1])
    if recovered is None:
        message = "La salida del modelo no es JSON utilizable"
        raise GenerationFormatError(message)
    return recovered


def _loads(text: str) -> object | None:
    try:
        loaded: object = json.loads(text)
    except json.JSONDecodeError:
        return None
    return loaded


def _strip_fence(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
