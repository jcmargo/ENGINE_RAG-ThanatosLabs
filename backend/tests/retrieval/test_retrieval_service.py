"""Servicio de recuperación con fakes: aislamiento, umbral, rondas y traza."""

import ast
import asyncio
import logging
from collections.abc import Sequence
from dataclasses import asdict, fields, replace
from uuid import UUID, uuid4

import pytest

from app.config import repository_root
from app.domain.entities import AuthPrincipal, RetrievableVersion, TenantContext
from app.domain.enums import ApiKeyScope, PrincipalType
from app.domain.errors import InvalidStateError, NotFoundError
from app.retrieval.contracts import (
    IDENTIFIER_HEADING_LIMIT,
    MAX_CANDIDATES,
    Evidence,
    RetrievalRequest,
    candidate_limit,
)
from app.retrieval.service import RetrievalService, normalize_question
from app.retrieval.vectors import VectorHit, VectorQuery, point_id

_QUESTION = "pregunta-secreta-no-registrar"
_EXCERPT = "extracto-secreto-no-registrar"
_VECTOR = (0.125, 0.0, 0.0, 0.0)


class _Embeddings:
    def __init__(self, vector: Sequence[float] = _VECTOR) -> None:
        self.calls: list[str] = []
        self._vector = list(vector)
        self.model_version = "embed-test-v1"
        self.dimension = len(self._vector)

    def embed_query(self, text: str) -> list[float]:
        self.calls.append(text)
        return list(self._vector)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        del texts
        message = "La recuperación no debe embeber documentos"
        raise AssertionError(message)


class _Store:
    def __init__(self, pages: Sequence[Sequence[VectorHit]]) -> None:
        self._pages = list(pages)
        self.limits: list[int] = []
        self.document_ids: list[tuple[UUID, ...]] = []
        self.queries: list[VectorQuery] = []
        self.calls = 0

    async def search(self, context: TenantContext, query: VectorQuery) -> tuple[VectorHit, ...]:
        del context
        self.limits.append(query.limit)
        self.document_ids.append(query.document_ids)
        self.queries.append(query)
        page = self._pages[self.calls] if self.calls < len(self._pages) else ()
        self.calls += 1
        return tuple(page)[: query.limit]


class _Catalog:
    def __init__(
        self,
        *,
        ready: set[UUID] | None = None,
        versions: dict[UUID, RetrievableVersion] | None = None,
    ) -> None:
        self.ready = set() if ready is None else ready
        self.versions = {} if versions is None else versions
        self.ready_calls: list[tuple[UUID, ...]] = []
        self.version_calls: list[tuple[UUID, ...]] = []

    async def find_ready_document_ids(self, document_ids: Sequence[UUID]) -> frozenset[UUID]:
        found = tuple(document_ids)
        self.ready_calls.append(found)
        return frozenset(document_id for document_id in found if document_id in self.ready)

    async def find_retrievable_versions(
        self, version_ids: Sequence[UUID]
    ) -> tuple[RetrievableVersion, ...]:
        found = tuple(version_ids)
        self.version_calls.append(found)
        return tuple(
            self.versions[version_id] for version_id in found if version_id in self.versions
        )


class _Reverse:
    def __init__(self) -> None:
        self.questions: list[str] = []

    def rerank(
        self,
        question: str,
        hits: Sequence[VectorHit],
        *,
        limit: int,
    ) -> tuple[VectorHit, ...]:
        self.questions.append(question)
        return tuple(reversed(tuple(hits)))[:limit]


def _context(tenant_id: UUID | None = None) -> TenantContext:
    tenant = uuid4() if tenant_id is None else tenant_id
    return TenantContext(
        tenant_id=tenant,
        principal=AuthPrincipal(
            type=PrincipalType.API_KEY,
            user_id=None,
            tenant_id=tenant,
            role=None,
            scopes=(ApiKeyScope.QUERY,),
        ),
    )


def _with_position(hit: VectorHit, position: int) -> VectorHit:
    return replace(hit, position=position)


def _version(
    document_id: UUID,
    version_id: UUID,
    *,
    name: str = "politica-vacaciones.md",
) -> RetrievableVersion:
    return RetrievableVersion(
        document_id=document_id,
        version_id=version_id,
        display_name=name,
    )


def _hit(
    context: TenantContext,
    *,
    document_id: UUID | None = None,
    version_id: UUID | None = None,
    chunk_id: str | None = None,
    text: str = _EXCERPT,
    score: float = 0.91,
    page_start: int = 1,
    section: str | None = "Vacaciones",
    tenant_id: UUID | None = None,
    source: str = "nombre-en-qdrant.md",
) -> VectorHit:
    document = uuid4() if document_id is None else document_id
    version = uuid4() if version_id is None else version_id
    chunk = chunk_id if chunk_id is not None else uuid4().hex
    owner = context.tenant_id if tenant_id is None else tenant_id
    return VectorHit(
        point_id=point_id(
            tenant_id=owner,
            document_id=document,
            document_version_id=version,
            chunk_id=chunk,
        ),
        score=score,
        tenant_id=owner,
        document_id=document,
        document_version_id=version,
        chunk_id=chunk,
        source=source,
        page_start=page_start,
        page_end=page_start,
        section=section,
        position=0,
        text=text,
        parser_version="parser-v1",
        chunker_version="chunker-v2",
        embedding_version="embed-test-v1",
        is_active=True,
    )


def _service(
    embeddings: _Embeddings,
    store: _Store,
    *,
    score: float = 0.5,
    max_question_chars: int = 2000,
    reranker: _Reverse | None = None,
) -> RetrievalService:
    return RetrievalService(
        embeddings,
        store,
        min_retrieval_score=score,
        index_version="index-v1",
        max_question_chars=max_question_chars,
        reranker=reranker,
    )


def test_request_has_no_tenant_and_score_is_not_confidence() -> None:
    assert "tenant_id" not in RetrievalRequest.__dataclass_fields__
    names = {field.name for field in fields(Evidence)}
    assert "score" in names
    assert "confidence" not in names
    assert RetrievalRequest(question="hola").top_k == 5


def test_question_normalization_keeps_words_and_case() -> None:
    assert normalize_question("  Hola   mundo \n") == "Hola mundo"
    assert normalize_question("a\u0301") == "á"


def test_letter_case_does_not_change_the_embedded_question() -> None:
    context = _context()
    embeddings = _Embeddings()
    service = _service(embeddings, _Store(()))

    for question in ("Teorema 8.4.4", "teorema 8.4.4", "TEOREMA 8.4.4"):
        asyncio.run(service.retrieve(context, _Catalog(), RetrievalRequest(question=question)))

    assert embeddings.calls == ["teorema 8.4.4", "teorema 8.4.4", "teorema 8.4.4"]


def test_empty_question_and_top_k_outside_range_are_rejected() -> None:
    context = _context()
    embeddings = _Embeddings()
    store = _Store(())
    service = _service(embeddings, store)
    catalog = _Catalog()

    async def _run(request: RetrievalRequest) -> None:
        await service.retrieve(context, catalog, request)

    for question in ("", "   \n\t"):
        with pytest.raises(InvalidStateError, match="vacía"):
            asyncio.run(_run(RetrievalRequest(question=question)))
    with pytest.raises(InvalidStateError, match="máximo"):
        asyncio.run(_run(RetrievalRequest(question="a" * 2001)))
    for top_k in (0, 21, True):
        with pytest.raises(InvalidStateError, match="top_k"):
            asyncio.run(_run(RetrievalRequest(question="hola", top_k=top_k)))

    assert embeddings.calls == []
    assert store.calls == 0


def test_normalized_question_is_embedded_once_and_limit_oversamples() -> None:
    context = _context()
    document_id = uuid4()
    version_id = uuid4()
    hit = _hit(context, document_id=document_id, version_id=version_id, score=0.9)
    embeddings = _Embeddings()
    store = _Store(((hit,),))
    catalog = _Catalog(
        versions={version_id: _version(document_id, version_id)},
    )
    service = _service(embeddings, store)

    result = asyncio.run(
        service.retrieve(context, catalog, RetrievalRequest(question="  Hola   mundo \n"))
    )

    assert embeddings.calls == ["hola mundo"]
    assert store.limits == [candidate_limit(5, round_index=0)]
    assert store.limits == [max(5 * 3, 20)]
    assert result.evidence[0].source == "politica-vacaciones.md"
    assert result.evidence[0].page_start == 1
    assert result.evidence[0].position == 1
    assert result.evidence[0].score == 0.9
    assert result.has_sufficient_evidence is True
    assert len(catalog.version_calls) == 1
    assert catalog.version_calls[0] == (version_id,)


def test_top_k_20_requests_sixty_candidates_and_2000_chars_fit() -> None:
    context = _context()
    embeddings = _Embeddings()
    store = _Store(((),))
    service = _service(embeddings, store)

    asyncio.run(
        service.retrieve(context, _Catalog(), RetrievalRequest(question="a" * 2000, top_k=20))
    )

    assert store.limits == [min(max(20 * 3, 20), MAX_CANDIDATES)]
    assert store.limits == [60]
    assert len(embeddings.calls[0]) == 2000


def test_smaller_question_limit_is_configurable() -> None:
    context = _context()
    service = _service(_Embeddings(), _Store(()), max_question_chars=10)

    with pytest.raises(InvalidStateError, match="máximo"):
        asyncio.run(service.retrieve(context, _Catalog(), RetrievalRequest(question="a" * 11)))


def test_foreign_hit_cannot_pass_even_if_the_store_returns_it() -> None:
    context = _context()
    other = uuid4()
    own_document = uuid4()
    own_version = uuid4()
    foreign_document = uuid4()
    foreign_version = uuid4()
    own = _hit(context, document_id=own_document, version_id=own_version, chunk_id="propio")
    foreign = _hit(
        context,
        document_id=foreign_document,
        version_id=foreign_version,
        chunk_id="ajeno",
        tenant_id=other,
        score=0.99,
        text="texto-de-otra-empresa",
    )
    catalog = _Catalog(
        versions={
            own_version: _version(own_document, own_version),
            foreign_version: _version(foreign_document, foreign_version, name="ajena.md"),
        }
    )
    store = _Store(((foreign, own),))

    result = asyncio.run(
        _service(_Embeddings(), store).retrieve(context, catalog, RetrievalRequest(question="hola"))
    )

    assert [item.chunk_id for item in result.evidence] == ["propio"]
    assert foreign_version not in catalog.version_calls[0]
    assert "ajena.md" not in {item.source for item in result.evidence}
    assert "texto-de-otra-empresa" not in {item.excerpt for item in result.evidence}


def test_old_version_marked_active_by_the_store_is_discarded() -> None:
    context = _context()
    document_id = uuid4()
    current_id = uuid4()
    old_id = uuid4()
    current = _hit(
        context,
        document_id=document_id,
        version_id=current_id,
        chunk_id="actual",
        score=0.7,
        page_start=1,
    )
    old = _hit(
        context,
        document_id=document_id,
        version_id=old_id,
        chunk_id="vieja",
        score=0.99,
        page_start=9,
    )
    catalog = _Catalog(versions={current_id: _version(document_id, current_id)})
    store = _Store(((old, current),))

    result = asyncio.run(
        _service(_Embeddings(), store).retrieve(context, catalog, RetrievalRequest(question="hola"))
    )

    assert [item.chunk_id for item in result.evidence] == ["actual"]
    assert result.evidence[0].document_version_id == current_id
    assert result.evidence[0].page_start == 1
    assert result.evidence[0].position == 1
    assert catalog.version_calls == [(old_id, current_id)]


def test_hit_whose_document_does_not_match_postgres_is_discarded() -> None:
    context = _context()
    version_id = uuid4()
    hit = _hit(context, version_id=version_id)
    catalog = _Catalog(versions={version_id: _version(uuid4(), version_id)})

    result = asyncio.run(
        _service(_Embeddings(), _Store(((hit,),))).retrieve(
            context, catalog, RetrievalRequest(question="hola")
        )
    )

    assert result.evidence == ()
    assert result.has_sufficient_evidence is False
    assert result.trace.hits_after_validation == 0


def test_filter_of_another_tenant_is_not_found_without_searching() -> None:
    context = _context()
    own = uuid4()
    foreign = uuid4()
    embeddings = _Embeddings()
    store = _Store(())
    catalog = _Catalog(ready={own})
    service = _service(embeddings, store)

    with pytest.raises(NotFoundError, match=r"^Documento no encontrado$") as error:
        asyncio.run(
            service.retrieve(
                context,
                catalog,
                RetrievalRequest(question=_QUESTION, document_ids=(own, foreign)),
            )
        )

    assert error.value.message == "Documento no encontrado"
    assert "tenant" not in error.value.message.lower()
    assert embeddings.calls == []
    assert store.calls == 0
    assert catalog.ready_calls == [(own, foreign)]


def test_ready_filter_is_deduplicated_and_passed_to_the_store() -> None:
    context = _context()
    document_id = uuid4()
    version_id = uuid4()
    hit = _hit(context, document_id=document_id, version_id=version_id)
    store = _Store(((hit,),))
    catalog = _Catalog(
        ready={document_id},
        versions={version_id: _version(document_id, version_id)},
    )

    asyncio.run(
        _service(_Embeddings(), store).retrieve(
            context,
            catalog,
            RetrievalRequest(question="hola", document_ids=(document_id, document_id)),
        )
    )

    assert catalog.ready_calls == [(document_id,)]
    assert store.document_ids == [(document_id,)]


def test_score_below_threshold_returns_no_sufficient_evidence() -> None:
    context = _context()
    document_id = uuid4()
    version_id = uuid4()
    hit = _hit(context, document_id=document_id, version_id=version_id, score=0.2)
    catalog = _Catalog(versions={version_id: _version(document_id, version_id)})
    embeddings = _Embeddings()

    result = asyncio.run(
        _service(embeddings, _Store(((hit,),)), score=0.857).retrieve(
            context, catalog, RetrievalRequest(question=_QUESTION)
        )
    )

    assert result.evidence == ()
    assert result.has_sufficient_evidence is False
    assert result.trace.hits_retrieved == 1
    assert result.trace.hits_after_validation == 1
    assert result.trace.hits_after_threshold == 0
    assert result.trace.returned == 0
    assert result.trace.min_retrieval_score == 0.857
    assert embeddings.calls == [_QUESTION]
    assert "confidence" not in result.trace.__dataclass_fields__


def test_candidate_floor_keeps_a_hit_between_the_two_floors() -> None:
    context = _context()
    document_id = uuid4()
    version_id = uuid4()
    hit = _hit(context, document_id=document_id, version_id=version_id, score=0.82)
    catalog = _Catalog(versions={version_id: _version(document_id, version_id)})
    service = _service(_Embeddings(), _Store(((hit,), (hit,))), score=0.857)

    kept = asyncio.run(
        service.retrieve(
            context,
            catalog,
            RetrievalRequest(question=_QUESTION),
            candidate_floor=0.80,
        )
    )
    dropped = asyncio.run(service.retrieve(context, catalog, RetrievalRequest(question=_QUESTION)))

    assert len(kept.evidence) == 1
    assert kept.evidence[0].score == 0.82
    assert kept.trace.min_retrieval_score == 0.857
    assert kept.trace.hits_after_threshold == 1
    assert dropped.evidence == ()
    assert dropped.trace.hits_after_threshold == 0


def test_containing_keeps_the_identifier_below_the_score_floor() -> None:
    context = _context()
    document_id = uuid4()
    labeled_version = uuid4()
    other_version = uuid4()
    labeled = _hit(
        context,
        document_id=document_id,
        version_id=labeled_version,
        chunk_id="teorema",
        text="Teorema 8.4.4 describe el grupo.",
        score=0.42,
    )
    other = _hit(
        context,
        document_id=document_id,
        version_id=other_version,
        chunk_id="otra",
        text="Otra página sin el número.",
        score=0.99,
    )
    catalog = _Catalog(
        versions={
            labeled_version: _version(document_id, labeled_version, name="algebra.pdf"),
            other_version: _version(document_id, other_version, name="algebra.pdf"),
        }
    )

    result = asyncio.run(
        _service(_Embeddings(), _Store(((other, labeled),)), score=0.857).retrieve(
            context,
            catalog,
            RetrievalRequest(question="teorema"),
            containing=("8.4.4",),
        )
    )

    assert len(result.evidence) == 1
    assert result.evidence[0].score == 0.42
    assert "8.4.4" in result.evidence[0].excerpt
    assert result.has_sufficient_evidence is True


def test_identifier_search_widens_when_the_first_window_has_no_heading() -> None:
    context = _context()
    document_id = uuid4()
    version_id = uuid4()
    examples = [
        _hit(
            context,
            document_id=document_id,
            version_id=version_id,
            chunk_id=f"ejemplo-{index}",
            text="EJEMPLO 7.4.4",
            score=0.9,
            page_start=546,
            section="EJEMPLO 7.4.4",
        )
        for index in range(MAX_CANDIDATES)
    ]
    theorem = _hit(
        context,
        document_id=document_id,
        version_id=version_id,
        chunk_id="teorema",
        text="Teorema 7.4.4 Sea A una matriz.",
        score=0.5,
        page_start=548,
        section="Teorema 7.4.4",
    )
    catalog = _Catalog(versions={version_id: _version(document_id, version_id, name="algebra.pdf")})
    store = _Store((examples, [*examples, theorem]))

    result = asyncio.run(
        _service(_Embeddings(), store, score=0.857).retrieve(
            context,
            catalog,
            RetrievalRequest(question="teorema 7.4.4"),
            containing=("7.4.4",),
        )
    )

    assert store.limits == [MAX_CANDIDATES, IDENTIFIER_HEADING_LIMIT]
    assert result.evidence[0].chunk_id == "teorema"
    assert result.evidence[0].page_start == 548


def test_identifier_search_stops_when_the_heading_is_already_visible() -> None:
    context = _context()
    document_id = uuid4()
    version_id = uuid4()
    mentions = [
        _hit(
            context,
            document_id=document_id,
            version_id=version_id,
            chunk_id=f"nota-{index}",
            text="nota 7.4.4 al margen",
            score=0.2,
        )
        for index in range(MAX_CANDIDATES - 1)
    ]
    theorem = _hit(
        context,
        document_id=document_id,
        version_id=version_id,
        chunk_id="teorema",
        text="Teorema 7.4.4 Sea A una matriz.",
        score=0.4,
        page_start=548,
        section="Teorema 7.4.4",
    )
    catalog = _Catalog(versions={version_id: _version(document_id, version_id, name="algebra.pdf")})
    store = _Store(([*mentions, theorem], ()))

    result = asyncio.run(
        _service(_Embeddings(), store, score=0.857).retrieve(
            context,
            catalog,
            RetrievalRequest(question="teorema 7.4.4"),
            containing=("7.4.4",),
        )
    )

    assert store.limits == [MAX_CANDIDATES]
    assert result.evidence[0].chunk_id == "teorema"


def test_theorem_lookup_skips_an_example_heading_outside_the_vector_window() -> None:
    context = _context()
    document_id = uuid4()
    version_id = uuid4()
    example = _hit(
        context,
        document_id=document_id,
        version_id=version_id,
        chunk_id="ejemplo",
        text="EJEMPLO 7.4.4",
        score=0.99,
        page_start=546,
        section="EJEMPLO 7.4.4",
    )
    theorem = _hit(
        context,
        document_id=document_id,
        version_id=version_id,
        chunk_id="teorema",
        text="Teorema 7.4.4 Sea A una matriz de n por n. Entonces es invertible.",
        score=0.4,
        page_start=548,
        section="Teorema 7.4.4",
    )
    catalog = _Catalog(versions={version_id: _version(document_id, version_id, name="algebra.pdf")})
    store = _Store(((example,), (theorem,)))

    result = asyncio.run(
        _service(_Embeddings(), store, score=0.857).retrieve(
            context,
            catalog,
            RetrievalRequest(question="TEOREMA 7.4.4"),
            containing=("7.4.4",),
        )
    )

    assert store.queries[1].section_query == "teorema 7.4.4"
    assert [item.chunk_id for item in result.evidence] == ["teorema"]
    assert "Sea A una matriz" in result.evidence[0].excerpt
    assert "EJEMPLO" not in result.evidence[0].excerpt


def test_theorem_lookup_abstains_when_the_section_is_missing() -> None:
    context = _context()
    document_id = uuid4()
    version_id = uuid4()
    example = _hit(
        context,
        document_id=document_id,
        version_id=version_id,
        chunk_id="ejemplo",
        text="EJEMPLO 7.4.4",
        score=0.99,
        section="EJEMPLO 7.4.4",
    )
    catalog = _Catalog(versions={version_id: _version(document_id, version_id, name="algebra.pdf")})
    store = _Store(((example,), ()))

    result = asyncio.run(
        _service(_Embeddings(), store, score=0.857).retrieve(
            context,
            catalog,
            RetrievalRequest(question="TEOREMA 7.4.4"),
            containing=("7.4.4",),
        )
    )

    assert result.evidence == ()
    assert result.has_sufficient_evidence is False


def test_heading_only_chunk_keeps_the_following_text() -> None:
    context = _context()
    document_id = uuid4()
    version_id = uuid4()
    heading = _hit(
        context,
        document_id=document_id,
        version_id=version_id,
        chunk_id="ejemplo",
        text="EJEMPLO 7.4.4",
        score=0.9,
        page_start=546,
        section="EJEMPLO 7.4.4",
    )
    heading = _with_position(heading, 10)
    body = _hit(
        context,
        document_id=document_id,
        version_id=version_id,
        chunk_id="cuerpo",
        text="Sea T una transformación de R3 en R2.",
        score=0.2,
        page_start=547,
        section="ejemplo",
    )
    body = _with_position(body, 11)
    catalog = _Catalog(versions={version_id: _version(document_id, version_id, name="algebra.pdf")})
    store = _Store(((heading,), (body,)))

    result = asyncio.run(
        _service(_Embeddings(), store, score=0.857).retrieve(
            context,
            catalog,
            RetrievalRequest(question="EJEMPLO 7.4.4"),
            containing=("7.4.4",),
        )
    )

    assert store.queries[1].position_gt == 10
    assert "EJEMPLO 7.4.4" in result.evidence[0].excerpt
    assert "transformación de R3" in result.evidence[0].excerpt
    assert result.evidence[0].page_end == 547


def test_trace_and_log_keep_versions_and_durations_without_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    context = _context()
    document_id = uuid4()
    version_id = uuid4()
    request_id = uuid4()
    hit = _hit(context, document_id=document_id, version_id=version_id, text=_EXCERPT, score=0.91)
    catalog = _Catalog(versions={version_id: _version(document_id, version_id)})
    service = _service(_Embeddings(), _Store(((hit,),)))

    with caplog.at_level(logging.INFO, logger="app.retrieval.service"):
        result = asyncio.run(
            service.retrieve(
                context,
                catalog,
                RetrievalRequest(question=_QUESTION, request_id=request_id),
            )
        )

    trace = result.trace
    dumped = asdict(trace)
    assert trace.request_id == request_id
    assert trace.embedding_version == "embed-test-v1"
    assert trace.index_version == "index-v1"
    assert trace.rounds == 1
    assert trace.embed_ms >= 0
    assert trace.search_ms >= 0
    assert trace.validate_ms >= 0
    blob = " ".join(str(value) for value in dumped.values())
    assert _QUESTION not in blob
    assert _EXCERPT not in blob
    assert "0.125" not in blob
    assert _QUESTION not in caplog.text
    assert _EXCERPT not in caplog.text
    assert "0.125" not in caplog.text
    assert "embed-test-v1" in caplog.text
    assert "index-v1" in caplog.text
    assert "request_id=" in caplog.text


def test_missing_request_id_is_generated_once() -> None:
    context = _context()
    document_id = uuid4()
    version_id = uuid4()
    hit = _hit(context, document_id=document_id, version_id=version_id)
    catalog = _Catalog(versions={version_id: _version(document_id, version_id)})

    result = asyncio.run(
        _service(_Embeddings(), _Store(((hit,),))).retrieve(
            context, catalog, RetrievalRequest(question="hola")
        )
    )

    assert isinstance(result.trace.request_id, UUID)


def test_search_stops_after_two_rounds_and_embeds_once() -> None:
    context = _context()
    first = tuple(_hit(context, chunk_id=f"r1-{index}", score=0.99) for index in range(20))
    second = tuple(_hit(context, chunk_id=f"r2-{index}", score=0.99) for index in range(40))
    valid = _hit(context, chunk_id="valida", score=0.95, page_start=2)
    page_two = (*second[:-1], valid)
    embeddings = _Embeddings()
    store = _Store((first, page_two, page_two))
    catalog = _Catalog(
        versions={valid.document_version_id: _version(valid.document_id, valid.document_version_id)}
    )

    result = asyncio.run(
        _service(embeddings, store).retrieve(
            context, catalog, RetrievalRequest(question="hola", top_k=5)
        )
    )

    assert embeddings.calls == ["hola"]
    assert store.calls == 2
    assert store.limits == [20, 40]
    assert len(catalog.version_calls) == 2
    assert len(catalog.version_calls[0]) == 20
    assert len(catalog.version_calls[1]) == 60
    assert [item.chunk_id for item in result.evidence] == ["valida"]
    assert result.evidence[0].page_start == 2
    assert result.trace.rounds == 2


def test_short_page_does_not_start_a_second_round() -> None:
    context = _context()
    hits = tuple(_hit(context, chunk_id=f"solo-{index}") for index in range(3))
    store = _Store((hits, hits))

    asyncio.run(
        _service(_Embeddings(), store).retrieve(
            context, _Catalog(), RetrievalRequest(question="hola")
        )
    )

    assert store.calls == 1


def test_order_follows_the_index_and_top_k_truncates() -> None:
    context = _context()
    first_document = uuid4()
    second_document = uuid4()
    first_version = uuid4()
    second_version = uuid4()
    third_document = uuid4()
    third_version = uuid4()
    hits = (
        _hit(
            context,
            document_id=first_document,
            version_id=first_version,
            chunk_id="primero",
            score=0.6,
        ),
        _hit(
            context,
            document_id=second_document,
            version_id=second_version,
            chunk_id="segundo",
            score=0.95,
        ),
        _hit(
            context,
            document_id=third_document,
            version_id=third_version,
            chunk_id="tercero",
            score=0.9,
        ),
    )
    catalog = _Catalog(
        versions={
            first_version: _version(first_document, first_version, name="a.md"),
            second_version: _version(second_document, second_version, name="b.md"),
            third_version: _version(third_document, third_version, name="c.md"),
        }
    )

    result = asyncio.run(
        _service(_Embeddings(), _Store((hits,))).retrieve(
            context, catalog, RetrievalRequest(question="hola", top_k=2)
        )
    )

    assert [item.chunk_id for item in result.evidence] == ["primero", "segundo"]
    assert [item.position for item in result.evidence] == [1, 2]
    assert result.trace.hits_after_threshold == 3
    assert result.trace.returned == 2


def test_injected_reranker_reorders_and_default_path_does_not_need_one() -> None:
    context = _context()
    first_document = uuid4()
    second_document = uuid4()
    first_version = uuid4()
    second_version = uuid4()
    hits = (
        _hit(context, document_id=first_document, version_id=first_version, chunk_id="primero"),
        _hit(context, document_id=second_document, version_id=second_version, chunk_id="segundo"),
    )
    catalog = _Catalog(
        versions={
            first_version: _version(first_document, first_version, name="a.md"),
            second_version: _version(second_document, second_version, name="b.md"),
        }
    )
    reranker = _Reverse()

    result = asyncio.run(
        _service(_Embeddings(), _Store((hits,)), reranker=reranker).retrieve(
            context, catalog, RetrievalRequest(question="  Hola  ")
        )
    )

    assert reranker.questions == ["hola"]
    assert [item.chunk_id for item in result.evidence] == ["segundo", "primero"]


def test_retrieval_module_does_not_call_a_language_model() -> None:
    path = repository_root() / "backend" / "app" / "retrieval" / "service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)

    assert all("generation" not in module and "ollama" not in module for module in modules)
