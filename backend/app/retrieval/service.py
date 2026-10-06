"""Recuperación densa: embedding, sobremuestreo y validación contra PostgreSQL."""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
import unicodedata
from collections.abc import Sequence
from dataclasses import replace
from uuid import UUID, uuid4

from app.domain.entities import RetrievableVersion, TenantContext
from app.domain.errors import InvalidStateError, NotFoundError
from app.domain.follow_up import FOLLOW_UP_PARTS, RECENT_TOPICS
from app.embeddings.base import EmbeddingProvider
from app.retrieval.contracts import (
    DEFAULT_MAX_QUESTION_CHARS,
    IDENTIFIER_HEADING_LIMIT,
    MAX_CANDIDATES,
    MAX_SEARCH_ROUNDS,
    MAX_TOP_K,
    MIN_TOP_K,
    SECTION_LOOKUP_LIMIT,
    CandidateReranker,
    CandidateRetriever,
    DocumentReadCatalog,
    Evidence,
    RetrievalRequest,
    RetrievalResult,
    RetrievalTrace,
    candidate_limit,
)
from app.retrieval.identifiers import (
    _STATEMENT_CHARS,
    contains_all,
    is_label_only,
    label_heading,
    named_label,
    opens_numbered_label,
    opens_with_kind,
    statement_text,
)
from app.retrieval.vectors import VectorHit, VectorQuery, VectorStore

logger = logging.getLogger(__name__)

_NOT_FOUND = "Documento no encontrado"
_PART_SPAN = 24
_PART_CHARS = 12_000
_STITCH_MIN_WORDS = 8
_AFTER_PROOF = re.compile(r"(?i)(?<=[.!?;])\s+(?=antes de dar ejemplos\b|procedimiento\b)")
_LINE_HYPHEN = re.compile(r"([A-Za-zÁÉÍÓÚÜÑáéíóúüñ])-\s+([a-záéíóúüñ])")
_PART_MARK = {
    "definicion": re.compile(r"(?i)(?:\n+\s*|\s{2,}|(?<=[.!?;])\s*)(definici[oó]n)\b"),
    "demostracion": re.compile(r"(?i)(?:\n+\s*|\s{2,}|(?<=[.!?;])\s*)(demostraci[oó]n)\b"),
    "ejemplo": re.compile(r"(?i)(?:\n+\s*|\s{2,}|(?<=[.!?;])\s*)(ejemplo)\b"),
}


class DenseCandidateRetriever:
    """Única recuperación implementada: búsqueda densa en el índice vectorial."""

    def __init__(self, store: VectorStore) -> None:
        self._store = store

    async def retrieve(
        self,
        context: TenantContext,
        vector: tuple[float, ...],
        *,
        limit: int,
        document_ids: tuple[UUID, ...],
    ) -> tuple[VectorHit, ...]:
        """Pide candidatos activos de la empresa. No reordena ni aplica el umbral."""
        return await self._store.search(
            context,
            VectorQuery(vector=vector, limit=limit, document_ids=document_ids),
        )


class RetrievalService:
    """Convierte una pregunta en evidencia de la empresa del contexto."""

    def __init__(
        self,
        embeddings: EmbeddingProvider,
        vectors: VectorStore,
        *,
        min_retrieval_score: float,
        index_version: str,
        max_question_chars: int = DEFAULT_MAX_QUESTION_CHARS,
        reranker: CandidateReranker | None = None,
    ) -> None:
        self._embeddings = embeddings
        self._vectors = vectors
        self._min_retrieval_score = _unit_score(min_retrieval_score)
        self._index_version = _require_index_version(index_version)
        self._max_question_chars = _require_max_chars(max_question_chars)
        self._reranker = reranker

    async def retrieve(
        self,
        context: TenantContext,
        documents: DocumentReadCatalog,
        request: RetrievalRequest,
        *,
        candidate_floor: float | None = None,
        containing: tuple[str, ...] = (),
    ) -> RetrievalResult:
        """Busca, valida versiones listas y deja traza sin el texto ni el vector.

        `candidate_floor` baja el corte para que la pregunta aplique los dos pisos.
        `containing` conserva el fragmento del identificador aunque su coseno sea bajo.
        """
        if not isinstance(context, TenantContext):
            message = "La recuperación exige un TenantContext"
            raise InvalidStateError(message)
        question = _require_question(
            request.question,
            max_chars=self._max_question_chars,
        ).casefold()
        top_k = _require_top_k(request.top_k)
        request_id = _require_request_id(request.request_id)
        document_ids = _unique_ids(request.document_ids)
        needles = _require_containing(containing)
        floor = (
            self._min_retrieval_score if candidate_floor is None else _unit_score(candidate_floor)
        )

        validate_ms = 0.0
        filter_started = time.perf_counter()
        if document_ids:
            ready = await documents.find_ready_document_ids(document_ids)
            if frozenset(document_ids) != ready:
                raise NotFoundError(_NOT_FOUND)
        validate_ms += _elapsed_ms(filter_started)

        embed_started = time.perf_counter()
        embedded = await asyncio.to_thread(self._embeddings.embed_query, question)
        vector = tuple(embedded)
        embed_ms = _elapsed_ms(embed_started)

        retriever: CandidateRetriever = DenseCandidateRetriever(self._vectors)
        ordered: list[VectorHit] = []
        seen_points: set[UUID] = set()
        accepted: list[tuple[VectorHit, RetrievableVersion]] = []
        rounds = 0
        limit = MAX_CANDIDATES if needles else candidate_limit(top_k, round_index=0)
        search_ms = 0.0
        while rounds < MAX_SEARCH_ROUNDS:
            search_started = time.perf_counter()
            hits = await retriever.retrieve(
                context,
                vector,
                limit=limit,
                document_ids=document_ids,
            )
            search_ms += _elapsed_ms(search_started)
            rounds += 1
            for hit in hits:
                if hit.point_id in seen_points:
                    continue
                seen_points.add(hit.point_id)
                ordered.append(hit)
            validate_started = time.perf_counter()
            accepted = await _matching_versions(documents, context, ordered)
            validate_ms += _elapsed_ms(validate_started)
            matched = _with_identifiers(accepted, needles) if needles else accepted
            if _needs_heading_window(needles, matched, hits, limit=limit, rounds=rounds):
                limit = IDENTIFIER_HEADING_LIMIT
                continue
            if len(matched) >= top_k or len(hits) < limit or rounds >= MAX_SEARCH_ROUNDS:
                break
            next_limit = candidate_limit(top_k, round_index=rounds)
            if next_limit <= limit:
                break
            limit = next_limit

        named = named_label(question)
        if named is not None and not _opens_named(accepted, needles, named):
            search_started = time.perf_counter()
            section_hits = await self._vectors.search(
                context,
                VectorQuery(
                    vector=vector,
                    limit=SECTION_LOOKUP_LIMIT,
                    document_ids=document_ids,
                    section_query=f"{named[0]} {named[1]}",
                ),
            )
            search_ms += _elapsed_ms(search_started)
            fresh = [hit for hit in section_hits if hit.point_id not in seen_points]
            if fresh:
                validate_started = time.perf_counter()
                extra = await _matching_versions(documents, context, fresh)
                validate_ms += _elapsed_ms(validate_started)
                accepted.extend(extra)
                for hit, _row in extra:
                    seen_points.add(hit.point_id)
                    ordered.append(hit)

        validated = len(accepted)
        if self._reranker is not None and accepted:
            accepted = _apply_reranker(self._reranker, question, accepted)
        if needles:
            passed = _heading_first(_with_identifiers(accepted, needles), needles)
        else:
            passed = [pair for pair in accepted if pair[0].score >= floor]
        if named is not None:
            passed = [pair for pair in passed if opens_with_kind(pair[0].text, named[0], named[1])]
        chosen = passed[:top_k]
        if named is not None and chosen and is_label_only(chosen[0][0].text):
            chosen = [
                await _continue_heading(
                    self._vectors,
                    context,
                    vector,
                    document_ids,
                    chosen[0],
                ),
                *chosen[1:],
            ]
        evidence = tuple(
            _evidence(hit, row, position) for position, (hit, row) in enumerate(chosen, start=1)
        )
        trace = RetrievalTrace(
            request_id=request_id,
            embedding_version=self._embeddings.model_version,
            index_version=self._index_version,
            hits_retrieved=len(ordered),
            hits_after_validation=validated,
            hits_after_threshold=len(passed),
            returned=len(evidence),
            rounds=rounds,
            top_k=top_k,
            min_retrieval_score=self._min_retrieval_score,
            embed_ms=embed_ms,
            search_ms=search_ms,
            validate_ms=validate_ms,
        )
        _log_trace(trace)
        return RetrievalResult(
            evidence=evidence,
            has_sufficient_evidence=bool(evidence),
            trace=trace,
        )

    async def retrieve_part(
        self,
        context: TenantContext,
        documents: DocumentReadCatalog,
        request: RetrievalRequest,
        *,
        part: str,
        topics: tuple[tuple[str, str], ...],
    ) -> RetrievalResult:
        """Trae el enunciado, la demostración o el ejemplo de un rótulo ya elegido.

        No aplica el filtro que exige abrir con el teorema ni el piso de coseno.
        Prueba los temas del más nuevo al más viejo y se queda con el primero
        que tenga esa parte.
        """
        if not isinstance(context, TenantContext):
            message = "La recuperación exige un TenantContext"
            raise InvalidStateError(message)
        question = _require_question(
            request.question,
            max_chars=self._max_question_chars,
        ).casefold()
        top_k = _require_top_k(request.top_k)
        request_id = _require_request_id(request.request_id)
        document_ids = _unique_ids(request.document_ids)
        labels = _clean_topics(topics)
        part_name = part.casefold() if isinstance(part, str) else ""

        validate_ms = 0.0
        search_ms = 0.0
        embed_ms = 0.0
        hits_retrieved = 0
        validated = 0
        chosen: list[tuple[VectorHit, RetrievableVersion]] = []
        if part_name in FOLLOW_UP_PARTS and labels:
            if document_ids:
                filter_started = time.perf_counter()
                ready = await documents.find_ready_document_ids(document_ids)
                validate_ms += _elapsed_ms(filter_started)
                if frozenset(document_ids) != ready:
                    raise NotFoundError(_NOT_FOUND)
            embed_started = time.perf_counter()
            embedded = await asyncio.to_thread(self._embeddings.embed_query, question)
            vector = tuple(embedded)
            embed_ms = _elapsed_ms(embed_started)
            for kind, number in labels:
                pieces, seen, accepted, part_search, part_validate = await _load_part(
                    self._vectors,
                    documents,
                    context,
                    vector,
                    document_ids,
                    kind,
                    number,
                    part_name,
                )
                hits_retrieved += seen
                validated += accepted
                search_ms += part_search
                validate_ms += part_validate
                if pieces:
                    chosen = pieces
                    break
        evidence = tuple(
            _evidence(hit, row, position) for position, (hit, row) in enumerate(chosen, start=1)
        )
        trace = RetrievalTrace(
            request_id=request_id,
            embedding_version=self._embeddings.model_version,
            index_version=self._index_version,
            hits_retrieved=hits_retrieved,
            hits_after_validation=validated,
            hits_after_threshold=len(evidence),
            returned=len(evidence),
            rounds=1,
            top_k=top_k,
            min_retrieval_score=self._min_retrieval_score,
            embed_ms=embed_ms,
            search_ms=search_ms,
            validate_ms=validate_ms,
        )
        _log_trace(trace)
        return RetrievalResult(
            evidence=evidence,
            has_sufficient_evidence=bool(evidence),
            trace=trace,
        )


def normalize_question(text: str) -> str:
    """Une espacios y normaliza Unicode sin cambiar palabras ni mayúsculas."""
    return " ".join(unicodedata.normalize("NFC", text).split())


async def _matching_versions(
    documents: DocumentReadCatalog,
    context: TenantContext,
    hits: Sequence[VectorHit],
) -> list[tuple[VectorHit, RetrievableVersion]]:
    scoped = [hit for hit in hits if hit.tenant_id == context.tenant_id]
    if not scoped:
        return []
    rows = await documents.find_retrievable_versions(
        tuple(hit.document_version_id for hit in scoped)
    )
    by_version = {row.version_id: row for row in rows}
    matched: list[tuple[VectorHit, RetrievableVersion]] = []
    for hit in scoped:
        row = by_version.get(hit.document_version_id)
        if row is None or row.document_id != hit.document_id:
            continue
        matched.append((hit, row))
    return matched


def _apply_reranker(
    reranker: CandidateReranker,
    question: str,
    accepted: Sequence[tuple[VectorHit, RetrievableVersion]],
) -> list[tuple[VectorHit, RetrievableVersion]]:
    by_point = {hit.point_id: (hit, row) for hit, row in accepted}
    reranked = reranker.rerank(question, tuple(hit for hit, _row in accepted), limit=len(accepted))
    ordered: list[tuple[VectorHit, RetrievableVersion]] = []
    seen: set[UUID] = set()
    for hit in reranked:
        if hit.point_id in seen or hit.point_id not in by_point:
            continue
        seen.add(hit.point_id)
        ordered.append(by_point[hit.point_id])
    return ordered


def _evidence(hit: VectorHit, row: RetrievableVersion, position: int) -> Evidence:
    return Evidence(
        chunk_id=hit.chunk_id,
        document_id=row.document_id,
        document_version_id=row.version_id,
        source=row.display_name,
        page_start=hit.page_start,
        page_end=hit.page_end,
        section=hit.section,
        excerpt=hit.text,
        score=hit.score,
        position=position,
    )


def _log_trace(trace: RetrievalTrace) -> None:
    from app.infrastructure.metrics import observe_retrieval

    observe_retrieval(
        kept=trace.returned,
        duration_ms=trace.embed_ms + trace.search_ms + trace.validate_ms,
    )
    logger.info(
        "recuperacion request_id=%s embedding=%s index=%s hits=%d validated=%d "
        "kept=%d rounds=%d top_k=%d threshold=%.3f embed_ms=%.3f search_ms=%.3f validate_ms=%.3f",
        trace.request_id,
        trace.embedding_version,
        trace.index_version,
        trace.hits_retrieved,
        trace.hits_after_validation,
        trace.returned,
        trace.rounds,
        trace.top_k,
        trace.min_retrieval_score,
        trace.embed_ms,
        trace.search_ms,
        trace.validate_ms,
    )


def _require_question(text: str, *, max_chars: int) -> str:
    if not isinstance(text, str):
        message = "La pregunta no es válida"
        raise InvalidStateError(message)
    normalized = normalize_question(text)
    if not normalized:
        message = "La pregunta no puede estar vacía"
        raise InvalidStateError(message)
    if len(normalized) > max_chars:
        message = "La pregunta supera el máximo permitido"
        raise InvalidStateError(message)
    return normalized


def _require_top_k(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_TOP_K <= value <= MAX_TOP_K:
        message = "top_k debe ser un entero entre 1 y 20"
        raise InvalidStateError(message)
    return value


def _require_request_id(value: UUID | None) -> UUID:
    if value is None:
        return uuid4()
    if not isinstance(value, UUID):
        message = "El identificador de la solicitud no es válido"
        raise InvalidStateError(message)
    return value


def _unique_ids(values: tuple[UUID, ...]) -> tuple[UUID, ...]:
    if isinstance(values, str) or not isinstance(values, tuple):
        message = "Los filtros de documento no son válidos"
        raise InvalidStateError(message)
    unique: list[UUID] = []
    seen: set[UUID] = set()
    for value in values:
        if not isinstance(value, UUID):
            message = "Los filtros de documento no son válidos"
            raise InvalidStateError(message)
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return tuple(unique)


def _require_index_version(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or "|" in value:
        message = "La revisión del índice no es válida"
        raise InvalidStateError(message)
    return value


def _require_max_chars(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        message = "El máximo de la pregunta debe ser un entero positivo"
        raise InvalidStateError(message)
    return value


def _require_containing(containing: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(containing, tuple):
        message = "El filtro de identificadores no es válido"
        raise InvalidStateError(message)
    for item in containing:
        if not isinstance(item, str) or not item.strip():
            message = "El filtro de identificadores no es válido"
            raise InvalidStateError(message)
    return containing


def _needs_heading_window(
    needles: tuple[str, ...],
    matched: Sequence[tuple[VectorHit, RetrievableVersion]],
    hits: Sequence[VectorHit],
    *,
    limit: int,
    rounds: int,
) -> bool:
    """La ventana corta no trajo el rótulo y todavía cabe una búsqueda más amplia."""
    return (
        bool(needles)
        and not _has_heading(matched, needles)
        and limit < IDENTIFIER_HEADING_LIMIT
        and len(hits) >= limit
        and rounds < MAX_SEARCH_ROUNDS
    )


def _has_heading(
    pairs: Sequence[tuple[VectorHit, RetrievableVersion]],
    needles: tuple[str, ...],
) -> bool:
    return any(label_heading(hit.text, needles) for hit, _row in pairs)


def _opens_named(
    accepted: Sequence[tuple[VectorHit, RetrievableVersion]],
    needles: tuple[str, ...],
    named: tuple[str, str],
) -> bool:
    kind, number = named
    return any(
        contains_all(hit.text, needles) and opens_with_kind(hit.text, kind, number)
        for hit, _row in accepted
    )


async def _continue_heading(
    store: VectorStore,
    context: TenantContext,
    vector: tuple[float, ...],
    document_ids: tuple[UUID, ...],
    pair: tuple[VectorHit, RetrievableVersion],
) -> tuple[VectorHit, RetrievableVersion]:
    """Si el fragmento es solo el rótulo, une los siguientes de la misma versión."""
    hit, row = pair
    followers = await store.search(
        context,
        VectorQuery(
            vector=vector,
            limit=8,
            document_ids=document_ids,
            document_version_ids=(hit.document_version_id,),
            position_gt=hit.position,
            position_lt=hit.position + 6,
        ),
    )
    text, page_end = _join_following(hit, followers)
    if text == hit.text and page_end == hit.page_end:
        return pair
    return replace(hit, text=text, page_end=page_end), row


def _join_following(hit: VectorHit, followers: Sequence[VectorHit]) -> tuple[str, int]:
    ordered = sorted(
        (item for item in followers if item.point_id != hit.point_id),
        key=lambda item: (item.position, item.chunk_id),
    )
    parts = [hit.text]
    total = len(hit.text)
    page_end = hit.page_end
    expected = hit.position + 1
    for item in ordered:
        if item.position < expected:
            continue
        body = item.text.strip()
        if item.position != expected or opens_numbered_label(body) or total >= _STATEMENT_CHARS:
            break
        if not body:
            expected += 1
            continue
        parts.append(body)
        total += len(body)
        page_end = max(page_end, item.page_end)
        expected += 1
    return "\n\n".join(parts), page_end


async def _load_part(
    store: VectorStore,
    documents: DocumentReadCatalog,
    context: TenantContext,
    vector: tuple[float, ...],
    document_ids: tuple[UUID, ...],
    kind: str,
    number: str,
    part: str,
) -> tuple[list[tuple[VectorHit, RetrievableVersion]], int, int, float, float]:
    """Fragmentos de una parte. Vacío si este tema no la tiene."""
    search_ms = 0.0
    validate_ms = 0.0
    seen = 0
    started = time.perf_counter()
    section_hits = await store.search(
        context,
        VectorQuery(
            vector=vector,
            limit=SECTION_LOOKUP_LIMIT,
            document_ids=document_ids,
            section_query=f"{kind} {number}",
        ),
    )
    search_ms += _elapsed_ms(started)
    seen += len(section_hits)
    validate_started = time.perf_counter()
    accepted = await _matching_versions(documents, context, section_hits)
    validate_ms += _elapsed_ms(validate_started)
    anchor = next(
        (pair for pair in accepted if opens_with_kind(pair[0].text, kind, number)),
        None,
    )
    if anchor is None:
        return [], seen, len(accepted), search_ms, validate_ms
    hit, row = anchor
    if part == "definicion":
        statement = statement_text(hit.text).strip()
        if not statement or is_label_only(statement):
            followers, follow_seen, follow_search, follow_validate = await _following(
                store,
                documents,
                context,
                vector,
                document_ids,
                hit,
            )
            seen += follow_seen
            search_ms += follow_search
            validate_ms += follow_validate
            statement = statement_text(_join_statement(hit, followers)).strip()
        if not statement or is_label_only(statement):
            return [], seen, len(accepted), search_ms, validate_ms
        return [(replace(hit, text=statement), row)], seen, len(accepted), search_ms, validate_ms
    followers, follow_seen, follow_search, follow_validate = await _following(
        store,
        documents,
        context,
        vector,
        document_ids,
        hit,
    )
    seen += follow_seen
    search_ms += follow_search
    validate_ms += follow_validate
    pieces = _collect_part(hit, followers, part, row)
    return pieces, seen, len(accepted) + len(followers), search_ms, validate_ms


async def _following(
    store: VectorStore,
    documents: DocumentReadCatalog,
    context: TenantContext,
    vector: tuple[float, ...],
    document_ids: tuple[UUID, ...],
    hit: VectorHit,
) -> tuple[tuple[tuple[VectorHit, RetrievableVersion], ...], int, float, float]:
    started = time.perf_counter()
    found = await store.search(
        context,
        VectorQuery(
            vector=vector,
            limit=_PART_SPAN,
            document_ids=document_ids,
            document_version_ids=(hit.document_version_id,),
            position_gt=hit.position,
            position_lt=hit.position + _PART_SPAN,
        ),
    )
    search_ms = _elapsed_ms(started)
    validate_started = time.perf_counter()
    matched = await _matching_versions(documents, context, found)
    return tuple(matched), len(found), search_ms, _elapsed_ms(validate_started)


def _join_statement(
    hit: VectorHit,
    followers: Sequence[tuple[VectorHit, RetrievableVersion]],
) -> str:
    """Une el enunciado que sigue a un rótulo vacío y se detiene en la prueba."""
    parts = [hit.text]
    total = len(hit.text)
    expected = hit.position + 1
    ordered = sorted(
        (item for item, _row in followers if item.point_id != hit.point_id),
        key=lambda item: (item.position, item.chunk_id),
    )
    for item in ordered:
        if item.position != expected or total >= _STATEMENT_CHARS:
            break
        body = item.text.strip()
        if not body:
            expected += 1
            continue
        if opens_numbered_label(body) or _is_later_part(item.section, body):
            break
        parts.append(body)
        total += len(body)
        expected += 1
    return "\n\n".join(parts)


def _collect_part(
    hit: VectorHit,
    followers: Sequence[tuple[VectorHit, RetrievableVersion]],
    part: str,
    row: RetrievableVersion,
) -> list[tuple[VectorHit, RetrievableVersion]]:
    """Fragmentos de la sección pedida, desde el enunciado hasta el rótulo siguiente."""
    pieces: list[tuple[VectorHit, RetrievableVersion]] = []
    total = 0
    tail = _part_tail(hit.text, part)
    if tail:
        pieces.append((replace(hit, text=tail), row))
        total += len(tail)
    started = bool(pieces)
    expected = hit.position + 1
    ordered = sorted(
        (
            item
            for item, _follower_row in followers
            if item.point_id != hit.point_id and item.document_version_id == hit.document_version_id
        ),
        key=lambda item: (item.position, item.chunk_id),
    )
    for item in ordered:
        if total >= _PART_CHARS:
            break
        if item.position < expected:
            continue
        if started and item.position != expected:
            break
        body = item.text.strip()
        if not body:
            if started:
                expected = item.position + 1
            continue
        if _section_matches(item.section, body, part):
            pieces.append((item, row))
            total += len(body)
            started = True
            expected = item.position + 1
            continue
        if started or opens_numbered_label(body):
            break
    return _finish_part(_stitch_pieces(pieces), part)


def _stitch_pieces(
    pieces: list[tuple[VectorHit, RetrievableVersion]],
) -> list[tuple[VectorHit, RetrievableVersion]]:
    """Une las ventanas solapadas de una misma sección en un solo fragmento."""
    if len(pieces) < 2:
        return pieces
    hit, row = pieces[0]
    text = hit.text.strip()
    page_end = hit.page_end
    for follower, _follower_row in pieces[1:]:
        page_end = max(page_end, follower.page_end)
        body = follower.text.strip()
        if not body:
            continue
        extra = _novel_words(text, body)
        if extra is None:
            text = f"{text}\n\n{body}"
        elif extra:
            text = f"{text} {extra}"
    if text == hit.text and page_end == hit.page_end:
        return pieces[:1]
    return [(replace(hit, text=text, page_end=page_end), row)]


def _finish_part(
    pieces: list[tuple[VectorHit, RetrievableVersion]],
    part: str,
) -> list[tuple[VectorHit, RetrievableVersion]]:
    """La demostración termina en la prueba, antes del procedimiento o los ejemplos."""
    if part != "demostracion" or not pieces:
        return pieces
    hit, row = pieces[0]
    text = _clip_proof(_join_line_breaks(hit.text))
    if text == hit.text:
        return pieces[:1]
    return [(replace(hit, text=text), row)]


def _clip_proof(text: str) -> str:
    match = _AFTER_PROOF.search(text)
    if match is None:
        return text.strip()
    return text[: match.start()].strip()


def _join_line_breaks(text: str) -> str:
    return _LINE_HYPHEN.sub(r"\1\2", text)


def _novel_words(previous: str, current: str) -> str | None:
    """Palabras nuevas del fragmento. None si no continúa el anterior."""
    previous_words = previous.split()
    current_words = current.split()
    limit = min(len(previous_words), len(current_words), 200)
    for size in range(limit, _STITCH_MIN_WORDS - 1, -1):
        if previous_words[-size:] == current_words[:size]:
            return " ".join(current_words[size:])
    return None


def _part_tail(text: str, part: str) -> str:
    folded = _fold_label(text.lstrip())
    if _starts_with_part(folded, part):
        body = text.strip()
        return "" if is_label_only(body) else body
    match = _PART_MARK[part].search(text)
    if match is None:
        return ""
    body = text[match.start(1) :].strip()
    if not body or is_label_only(body):
        return ""
    return body


def _is_later_part(section: str | None, text: str) -> bool:
    return _section_matches(section, text, "demostracion") or _section_matches(
        section, text, "ejemplo"
    )


def _section_matches(section: str | None, text: str, part: str) -> bool:
    if section is not None and _starts_with_part(_fold_label(section), part):
        return True
    return _starts_with_part(_fold_label(text.lstrip()), part)


def _starts_with_part(folded: str, part: str) -> bool:
    return folded == part or folded.startswith((f"{part} ", f"{part}\n", f"{part}.", f"{part}:"))


def _fold_label(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    return "".join(character for character in normalized if not unicodedata.combining(character))


def _clean_topics(topics: Sequence[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    cleaned: list[tuple[str, str]] = []
    for item in topics:
        if not isinstance(item, tuple) or len(item) != 2:
            continue
        kind, number = item
        if not isinstance(kind, str) or not isinstance(number, str):
            continue
        folded = kind.strip().casefold()
        label = number.strip()
        if not folded or not label or (folded, label) in cleaned:
            continue
        cleaned.append((folded, label))
        if len(cleaned) == RECENT_TOPICS:
            break
    return tuple(cleaned)


def _heading_first(
    pairs: list[tuple[VectorHit, RetrievableVersion]],
    needles: tuple[str, ...],
) -> list[tuple[VectorHit, RetrievableVersion]]:
    """El enunciado del rótulo va delante de una mención que solo trae el número."""
    headed = [pair for pair in pairs if label_heading(pair[0].text, needles)]
    if not headed:
        return pairs
    headed_ids = {pair[0].point_id for pair in headed}
    rest = [pair for pair in pairs if pair[0].point_id not in headed_ids]
    return headed + rest


def _with_identifiers(
    accepted: list[tuple[VectorHit, RetrievableVersion]],
    needles: tuple[str, ...],
) -> list[tuple[VectorHit, RetrievableVersion]]:
    return [pair for pair in accepted if contains_all(pair[0].text, needles)]


def _unit_score(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        message = "min_retrieval_score debe ser un número entre 0 y 1"
        raise InvalidStateError(message)
    score = float(value)
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        message = "min_retrieval_score debe ser un número entre 0 y 1"
        raise InvalidStateError(message)
    return score


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
