"""La parte pedida sale por sección y posición, no por el filtro del teorema."""

import asyncio
import unicodedata
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from app.domain.entities import AuthPrincipal, RetrievableVersion, TenantContext
from app.domain.enums import ApiKeyScope, PrincipalType
from app.generation.fake import FakeLLMProvider
from app.questions.models import QuestionRequest
from app.questions.service import QuestionService
from app.retrieval.contracts import RetrievalRequest
from app.retrieval.service import RetrievalService
from app.retrieval.vectors import VectorHit, VectorQuery, point_id

_STATEMENT = (
    "Teorema 8.4.4 Sea A una matriz real de n x n. "
    "Entonces A es diagonalizable ortogonalmente si y sólo si A es simétrica."
)
_PROOF = (
    "Demostración Supongamos que A es diagonalizable ortogonalmente "
    "y que el número 8.4.4 queda en el fragmento de la prueba."
)
_EXAMPLE = "Ejemplo 8.4.4 Una matriz diagonal ilustra el teorema."
_NEXT = "Teorema 8.4.5 Este otro teorema no entra en la respuesta."
_STATEMENT_744 = "Teorema 7.4.4 El resumen de las once afirmaciones equivalentes."
_PROOF_744 = "Demostración Esta prueba pertenece solo al teorema 7.4.4."
_COMBINED = (
    "Teorema 8.4.4 Sea A una matriz real. Entonces A es simétrica."
    "\n\nDemostración Supongamos que esta frase es la prueba."
)


class _Embeddings:
    model_version = "embed-test"
    dimension = 4

    def embed_query(self, text: str) -> list[float]:
        del text
        return [0.2, 0.0, 0.0, 0.0]


class _Store:
    def __init__(self, hits: Sequence[VectorHit]) -> None:
        self._hits = list(hits)

    async def search(self, context: TenantContext, query: VectorQuery) -> tuple[VectorHit, ...]:
        del context
        matched = [hit for hit in self._hits if _accepts(hit, query)]
        matched.sort(key=lambda item: (item.position, item.chunk_id))
        return tuple(matched[: query.limit])


class _Catalog:
    def __init__(self, versions: dict[UUID, RetrievableVersion]) -> None:
        self.versions = versions

    async def find_ready_document_ids(self, document_ids: Sequence[UUID]) -> frozenset[UUID]:
        del document_ids
        return frozenset()

    async def find_retrievable_versions(
        self, version_ids: Sequence[UUID]
    ) -> tuple[RetrievableVersion, ...]:
        return tuple(
            self.versions[version_id] for version_id in version_ids if version_id in self.versions
        )


class _Opener:
    def __init__(self, catalog: _Catalog) -> None:
        self._catalog = catalog

    @asynccontextmanager
    async def open(self, context: TenantContext) -> AsyncIterator[_Catalog]:
        del context
        yield self._catalog


def test_a_demonstration_returns_the_proof_and_not_the_statement() -> None:
    service, versions, context = _ready()
    result = asyncio.run(
        service.retrieve_part(
            context,
            _catalog(versions),
            RetrievalRequest(question="TeoREma 8.4.4\ndemostracion"),
            part="demostracion",
            topics=(("teorema", "8.4.4"), ("teorema", "7.4.4")),
        )
    )
    excerpts = " ".join(item.excerpt for item in result.evidence)

    assert result.has_sufficient_evidence
    assert "fragmento de la prueba" in excerpts
    assert "Sea A una matriz" not in excerpts
    assert "7.4.4" not in excerpts
    assert "8.4.5" not in excerpts
    assert result.evidence[0].score < 0.857


def test_overlapping_proof_windows_are_published_once() -> None:
    context = _context()
    document_id = uuid4()
    version_id = uuid4()
    version = RetrievableVersion(
        document_id=document_id,
        version_id=version_id,
        display_name="algebra.pdf",
    )
    shared = "suponga que A es diagonalizable ortogonalmente entonces existe una matriz"
    proof = f"Demostración Sea A simétrica. {shared} ortogonal."
    rest = f"{shared} ortogonal. El teorema queda demostrado."
    hits = (
        _hit(
            context,
            document_id=document_id,
            version_id=version_id,
            chunk_id="enunciado",
            text=_STATEMENT,
            section="Teorema 8.4.4",
            position=10,
            page_start=612,
        ),
        _hit(
            context,
            document_id=document_id,
            version_id=version_id,
            chunk_id="prueba-a",
            text=proof,
            section="Demostración",
            position=11,
            page_start=612,
        ),
        _hit(
            context,
            document_id=document_id,
            version_id=version_id,
            chunk_id="prueba-b",
            text=rest,
            section="Demostración",
            position=12,
            page_start=612,
        ),
        _hit(
            context,
            document_id=document_id,
            version_id=version_id,
            chunk_id="ejemplo-siguiente",
            text="Ejemplo 8.4.5 Este ejemplo no entra en la prueba.",
            section="Ejemplo 8.4.5",
            position=13,
            page_start=613,
        ),
    )
    service = RetrievalService(
        _Embeddings(),
        _Store(hits),
        min_retrieval_score=0.857,
        index_version="index-v1",
    )
    result = asyncio.run(
        service.retrieve_part(
            context,
            _Catalog({version_id: version}),
            RetrievalRequest(question="DEMOSTRACION"),
            part="demostracion",
            topics=(("teorema", "8.4.4"),),
        )
    )

    assert len(result.evidence) == 1
    excerpt = result.evidence[0].excerpt
    assert excerpt.count("suponga que A es diagonalizable") == 1
    assert "Sea A simétrica" in excerpt
    assert "queda demostrado" in excerpt
    assert "no entra en la prueba" not in excerpt


def test_a_demonstration_stops_before_the_following_procedure() -> None:
    context = _context()
    document_id = uuid4()
    version_id = uuid4()
    version = RetrievableVersion(
        document_id=document_id,
        version_id=version_id,
        display_name="algebra.pdf",
    )
    shared = "suponga que A es diagonalizable ortogonalmente entonces existe una matriz"
    opening = f"Demostración Sea A simétrica. En- tonces A es diagonalizable. {shared} ortogonal."
    closing = (
        f"{shared} ortogonal. Así, A es simétrica y el teorema queda demostrado. "
        "y D = D para cualquier matriz diagonal D. "
        "Antes de dar ejemplos, se proporciona el siguiente procedimiento. "
        "Diagonalización de una matriz simétrica."
    )
    hits = (
        _hit(
            context,
            document_id=document_id,
            version_id=version_id,
            chunk_id="enunciado",
            text=_STATEMENT,
            section="Teorema 8.4.4",
            position=10,
            page_start=612,
        ),
        _hit(
            context,
            document_id=document_id,
            version_id=version_id,
            chunk_id="prueba-a",
            text=opening,
            section="Demostración",
            position=11,
            page_start=612,
        ),
        _hit(
            context,
            document_id=document_id,
            version_id=version_id,
            chunk_id="prueba-b",
            text=closing,
            section="Demostración",
            position=12,
            page_start=612,
        ),
    )
    service = RetrievalService(
        _Embeddings(),
        _Store(hits),
        min_retrieval_score=0.857,
        index_version="index-v1",
    )
    result = asyncio.run(
        service.retrieve_part(
            context,
            _Catalog({version_id: version}),
            RetrievalRequest(question="DEMOSTRACION"),
            part="demostracion",
            topics=(("teorema", "8.4.4"),),
        )
    )
    excerpt = result.evidence[0].excerpt

    assert excerpt.endswith("matriz diagonal D.")
    assert "Entonces A es diagonalizable" in excerpt
    assert "En- tonces" not in excerpt
    assert "queda demostrado" in excerpt
    assert "Antes de dar ejemplos" not in excerpt
    assert "procedimiento" not in excerpt
    assert "Diagonalización" not in excerpt


def test_a_definition_returns_the_statement() -> None:
    service, versions, context = _ready()
    result = asyncio.run(
        service.retrieve_part(
            context,
            _catalog(versions),
            RetrievalRequest(question="definicion"),
            part="definicion",
            topics=(("teorema", "8.4.4"),),
        )
    )
    excerpts = " ".join(item.excerpt for item in result.evidence)

    assert "Sea A una matriz" in excerpts
    assert "fragmento de la prueba" not in excerpts


def test_an_example_returns_the_example_section() -> None:
    service, versions, context = _ready()
    result = asyncio.run(
        service.retrieve_part(
            context,
            _catalog(versions),
            RetrievalRequest(question="ejemplo"),
            part="ejemplo",
            topics=(("teorema", "8.4.4"),),
        )
    )
    excerpts = " ".join(item.excerpt for item in result.evidence)

    assert "matriz diagonal ilustra" in excerpts
    assert "fragmento de la prueba" not in excerpts
    assert "Sea A una matriz" not in excerpts


def test_the_previous_topic_is_used_only_when_the_recent_one_has_no_part() -> None:
    service, versions, context = _ready(include_recent_proof=False)
    missing = asyncio.run(
        service.retrieve_part(
            context,
            _catalog(versions),
            RetrievalRequest(question="demostracion"),
            part="demostracion",
            topics=(("teorema", "8.4.4"),),
        )
    )
    fallback = asyncio.run(
        service.retrieve_part(
            context,
            _catalog(versions),
            RetrievalRequest(question="demostracion"),
            part="demostracion",
            topics=(("teorema", "8.4.4"), ("teorema", "7.4.4")),
        )
    )

    assert missing.evidence == ()
    assert "7.4.4" in fallback.evidence[0].excerpt
    assert "Sea A una matriz" not in fallback.evidence[0].excerpt


def test_a_combined_chunk_keeps_the_statement_and_the_proof_apart() -> None:
    context = _context()
    document_id = uuid4()
    version_id = uuid4()
    version = RetrievableVersion(
        document_id=document_id,
        version_id=version_id,
        display_name="algebra.pdf",
    )
    hit = _hit(
        context,
        document_id=document_id,
        version_id=version_id,
        chunk_id="combinado",
        text=_COMBINED,
        section="Teorema 8.4.4",
        position=3,
        page_start=612,
    )
    service = RetrievalService(
        _Embeddings(),
        _Store((hit,)),
        min_retrieval_score=0.857,
        index_version="index-v1",
    )
    catalog = _Catalog({version_id: version})
    definition = asyncio.run(
        service.retrieve_part(
            context,
            catalog,
            RetrievalRequest(question="definicion"),
            part="definicion",
            topics=(("teorema", "8.4.4"),),
        )
    )
    proof = asyncio.run(
        service.retrieve_part(
            context,
            catalog,
            RetrievalRequest(question="demostracion"),
            part="demostracion",
            topics=(("teorema", "8.4.4"),),
        )
    )

    assert "Sea A una matriz" in definition.evidence[0].excerpt
    assert "esta frase es la prueba" not in definition.evidence[0].excerpt
    assert "esta frase es la prueba" in proof.evidence[0].excerpt
    assert "Sea A una matriz" not in proof.evidence[0].excerpt


def test_a_plain_theorem_question_still_returns_the_statement() -> None:
    service, versions, context = _ready()
    result = asyncio.run(
        service.retrieve(
            context,
            _catalog(versions),
            RetrievalRequest(question="Teorema 8.4.4"),
            containing=("8.4.4",),
        )
    )
    excerpts = " ".join(item.excerpt for item in result.evidence)

    assert "Sea A una matriz" in excerpts
    assert "fragmento de la prueba" not in excerpts


def test_the_question_service_publishes_the_proof_without_the_model() -> None:
    retrieval, versions, context = _ready()
    llm = FakeLLMProvider(())
    questions = QuestionService(
        retrieval,
        llm,
        _Opener(_catalog(versions)),
        max_context_chars=4000,
        min_retrieval_score=0.857,
        min_lexical_score=0.8,
    )
    result = asyncio.run(
        questions.answer(
            context,
            QuestionRequest(question="demostracion"),
            retrieval_query="TeoREma 8.4.4\ndemostracion",
            part="demostracion",
            topics=(("teorema", "8.4.4"), ("teorema", "7.4.4")),
        )
    )

    assert result.abstained is False
    assert llm.requests == []
    assert "fragmento de la prueba" in result.answer
    assert "Sea A una matriz" not in result.answer
    assert "7.4.4" not in result.answer
    assert result.citations[0].page == 612
    assert result.citations[0].document_name == "algebra.pdf"


def _ready(
    *, include_recent_proof: bool = True
) -> tuple[RetrievalService, dict[UUID, RetrievableVersion], TenantContext]:
    context = _context()
    recent_document = uuid4()
    recent_version = uuid4()
    older_document = uuid4()
    older_version = uuid4()
    hits = [
        _hit(
            context,
            document_id=recent_document,
            version_id=recent_version,
            chunk_id="enunciado-844",
            text=_STATEMENT,
            section="Teorema 8.4.4",
            position=10,
            page_start=612,
        ),
        _hit(
            context,
            document_id=recent_document,
            version_id=recent_version,
            chunk_id="ejemplo-844",
            text=_EXAMPLE,
            section="Ejemplo 8.4.4",
            position=12,
            page_start=612,
        ),
        _hit(
            context,
            document_id=recent_document,
            version_id=recent_version,
            chunk_id="siguiente",
            text=_NEXT,
            section="Teorema 8.4.5",
            position=13,
            page_start=613,
        ),
        _hit(
            context,
            document_id=older_document,
            version_id=older_version,
            chunk_id="enunciado-744",
            text=_STATEMENT_744,
            section="Teorema 7.4.4",
            position=4,
            page_start=548,
        ),
        _hit(
            context,
            document_id=older_document,
            version_id=older_version,
            chunk_id="prueba-744",
            text=_PROOF_744,
            section="Demostración",
            position=5,
            page_start=548,
        ),
    ]
    if include_recent_proof:
        hits.insert(
            1,
            _hit(
                context,
                document_id=recent_document,
                version_id=recent_version,
                chunk_id="prueba-844",
                text=_PROOF,
                section="Demostración",
                position=11,
                page_start=612,
            ),
        )
    versions = {
        recent_version: RetrievableVersion(
            document_id=recent_document,
            version_id=recent_version,
            display_name="algebra.pdf",
        ),
        older_version: RetrievableVersion(
            document_id=older_document,
            version_id=older_version,
            display_name="algebra.pdf",
        ),
    }
    service = RetrievalService(
        _Embeddings(),
        _Store(hits),
        min_retrieval_score=0.857,
        index_version="index-v1",
    )
    return service, versions, context


def _catalog(versions: dict[UUID, RetrievableVersion]) -> _Catalog:
    return _Catalog(versions)


def _context() -> TenantContext:
    tenant = uuid4()
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


def _hit(
    context: TenantContext,
    *,
    document_id: UUID,
    version_id: UUID,
    chunk_id: str,
    text: str,
    section: str,
    position: int,
    page_start: int,
) -> VectorHit:
    return VectorHit(
        point_id=point_id(
            tenant_id=context.tenant_id,
            document_id=document_id,
            document_version_id=version_id,
            chunk_id=chunk_id,
        ),
        score=0.2,
        tenant_id=context.tenant_id,
        document_id=document_id,
        document_version_id=version_id,
        chunk_id=chunk_id,
        source="nombre-en-qdrant.pdf",
        page_start=page_start,
        page_end=page_start,
        section=section,
        position=position,
        text=text,
        parser_version="parser-v1",
        chunker_version="chunker-v2",
        embedding_version="embed-test",
        is_active=True,
    )


def _accepts(hit: VectorHit, query: VectorQuery) -> bool:
    if query.document_ids and hit.document_id not in query.document_ids:
        return False
    if query.document_version_ids and hit.document_version_id not in query.document_version_ids:
        return False
    if query.section_query is not None and not _phrase(hit.section, query.section_query):
        return False
    if query.position_gt is not None and hit.position <= query.position_gt:
        return False
    return query.position_lt is None or hit.position < query.position_lt


def _phrase(section: str | None, query: str) -> bool:
    if section is None:
        return False
    return _fold(query) in _fold(section)


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    return "".join(character for character in normalized if not unicodedata.combining(character))
