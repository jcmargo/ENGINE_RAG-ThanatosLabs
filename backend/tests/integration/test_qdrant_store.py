"""Índice Qdrant real. Borra solo la colección y el alias de esta prueba."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import cast
from uuid import UUID, uuid4

import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    DeleteAlias,
    DeleteAliasOperation,
    Distance,
    PointStruct,
    VectorParams,
)

from app.config import get_settings
from app.domain.entities import AuthPrincipal, TenantContext
from app.domain.enums import ApiKeyScope, PrincipalType
from app.infrastructure.qdrant_store import QdrantVectorStore
from app.infrastructure.vector_client import create_qdrant_client
from app.retrieval.errors import IncompatibleVectorIndexError
from app.retrieval.vectors import VectorQuery, VectorRecord, collection_name, point_id

pytestmark = pytest.mark.integration

_MODEL = "spec09-fake"
_REVISION = "spec09-test"
_DIMENSION = 8
_ALIAS = "engine_rag_spec09_test"
_PROBE_COLLECTION = "engine_rag_probe"
_NEIGHBOR = "engine_rag_spec09_neighbor"
_VECTOR = (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
_PAYLOAD_FIELDS = {
    "tenant_id",
    "document_id",
    "document_version_id",
    "chunk_id",
    "is_active",
}


class _BatchSpy:
    """Cuenta los lotes que el almacén envía, y delega el resto al cliente real."""

    def __init__(self, client: AsyncQdrantClient, batches: list[int]) -> None:
        self._client = client
        self._batches = batches

    def __getattr__(self, name: str) -> object:
        return getattr(self._client, name)

    async def upsert(
        self,
        collection_name: str,
        points: Sequence[PointStruct],
        wait: bool = True,
    ) -> object:
        self._batches.append(len(points))
        return await self._client.upsert(
            collection_name=collection_name,
            points=points,
            wait=wait,
        )


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


def _record(
    context: TenantContext,
    *,
    document_id: UUID,
    version_id: UUID,
    chunk_id: str,
    text: str,
    source: str = "manual.pdf",
    page_start: int = 2,
    page_end: int = 3,
    section: str | None = "Vacaciones",
    position: int = 1,
) -> VectorRecord:
    return VectorRecord(
        tenant_id=context.tenant_id,
        document_id=document_id,
        document_version_id=version_id,
        chunk_id=chunk_id,
        source=source,
        page_start=page_start,
        page_end=page_end,
        section=section,
        position=position,
        text=text,
        parser_version="parser-v1",
        chunker_version="chunker-v2",
        embedding_version="deterministic-fake-v1",
        vector=_VECTOR,
    )


def _store(client: AsyncQdrantClient, *, batch_size: int = 64) -> QdrantVectorStore:
    return QdrantVectorStore(
        client,
        embedding_model=_MODEL,
        revision=_REVISION,
        dimension=_DIMENSION,
        alias_name=_ALIAS,
        batch_size=batch_size,
    )


def _expected_collection() -> str:
    return collection_name(embedding_model=_MODEL, revision=_REVISION, dimension=_DIMENSION)


async def _names(client: AsyncQdrantClient) -> set[str]:
    response = await client.get_collections()
    return {item.name for item in response.collections}


async def _remove_test_index(client: AsyncQdrantClient) -> None:
    aliases = await client.get_aliases()
    if any(item.alias_name == _ALIAS for item in aliases.aliases):
        await client.update_collection_aliases(
            change_aliases_operations=[
                DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=_ALIAS))
            ]
        )
    collection = _expected_collection()
    if await client.collection_exists(collection):
        await client.delete_collection(collection)
    aliases_after = await client.get_aliases()
    if any(item.alias_name == _ALIAS for item in aliases_after.aliases):
        message = "El alias de prueba sigue en Qdrant"
        raise AssertionError(message)


@asynccontextmanager
async def _index(
    *,
    batch_size: int = 64,
    batches: list[int] | None = None,
) -> AsyncIterator[tuple[QdrantVectorStore, AsyncQdrantClient]]:
    client = create_qdrant_client(get_settings().qdrant_url, timeout_seconds=30)
    bound = client
    if batches is not None:
        bound = cast(AsyncQdrantClient, _BatchSpy(client, batches))
    store = _store(bound, batch_size=batch_size)
    await _remove_test_index(client)
    preserved = await _names(client)
    try:
        yield store, client
    finally:
        await _remove_test_index(client)
        remaining = await _names(client)
        await client.close()
        missing = preserved - remaining
        if missing:
            message = f"La prueba borró colecciones ajenas: {sorted(missing)}"
            raise AssertionError(message)
        if _expected_collection() in remaining:
            message = "La colección de prueba sigue en Qdrant"
            raise AssertionError(message)


def test_ensure_index_creates_collection_payload_indexes_and_alias() -> None:
    asyncio.run(_creates_index())


async def _creates_index() -> None:
    async with _index() as (store, client):
        await store.ensure_index()
        info = await client.get_collection(store.collection_name)
        vectors = info.config.params.vectors
        assert isinstance(vectors, VectorParams)
        assert vectors.size == _DIMENSION
        assert vectors.distance == Distance.COSINE
        metadata = info.config.metadata or {}
        assert metadata["embedding_model"] == _MODEL
        assert metadata["revision"] == _REVISION
        assert metadata["distance"] == "cosine"
        assert metadata["dimension"] in {_DIMENSION, str(_DIMENSION)}
        assert set(info.payload_schema) >= _PAYLOAD_FIELDS
        aliases = await client.get_aliases()
        ours = [item for item in aliases.aliases if item.alias_name == _ALIAS]
        assert [item.collection_name for item in ours] == [store.collection_name]
        context = _context()
        assert await store.count(context) == 0
        await store.ensure_index()
        assert await store.count(context) == 0
        again = await client.get_collection(store.collection_name)
        assert set(again.payload_schema) >= _PAYLOAD_FIELDS


def test_incompatible_collection_is_rejected_and_preserved() -> None:
    asyncio.run(_rejects_incompatible())


async def _rejects_incompatible() -> None:
    async with _index() as (store, client):
        await _reject_dimension(store, client)
        await client.delete_collection(store.collection_name)
        await _reject_distance(store, client)
        await client.delete_collection(store.collection_name)
        await _reject_descriptor(store, client)


async def _reject_dimension(store: QdrantVectorStore, client: AsyncQdrantClient) -> None:
    await client.create_collection(
        collection_name=store.collection_name,
        vectors_config=VectorParams(size=4, distance=Distance.DOT),
        metadata={"embedding_model": "otro", "revision": "otra", "dimension": 4, "distance": "dot"},
    )
    before = await client.get_collection(store.collection_name)
    with pytest.raises(IncompatibleVectorIndexError, match="dimensión 8") as caught:
        await store.ensure_index()
    assert "4" in str(caught.value)
    assert caught.value.code == "incompatible_vector_index"
    after = await client.get_collection(store.collection_name)
    before_vectors = before.config.params.vectors
    after_vectors = after.config.params.vectors
    assert isinstance(before_vectors, VectorParams)
    assert isinstance(after_vectors, VectorParams)
    assert after_vectors.size == before_vectors.size == 4
    assert after_vectors.distance == before_vectors.distance == Distance.DOT
    assert after.config.metadata == before.config.metadata


async def _reject_distance(store: QdrantVectorStore, client: AsyncQdrantClient) -> None:
    await client.create_collection(
        collection_name=store.collection_name,
        vectors_config=VectorParams(size=_DIMENSION, distance=Distance.DOT),
        metadata={
            "embedding_model": _MODEL,
            "revision": _REVISION,
            "dimension": _DIMENSION,
            "distance": "dot",
        },
    )
    with pytest.raises(IncompatibleVectorIndexError, match="cosine"):
        await store.ensure_index()
    info = await client.get_collection(store.collection_name)
    vectors = info.config.params.vectors
    assert isinstance(vectors, VectorParams)
    assert vectors.distance == Distance.DOT
    assert vectors.size == _DIMENSION


async def _reject_descriptor(store: QdrantVectorStore, client: AsyncQdrantClient) -> None:
    await client.create_collection(
        collection_name=store.collection_name,
        vectors_config=VectorParams(size=_DIMENSION, distance=Distance.COSINE),
        metadata={
            "embedding_model": "otro-modelo",
            "revision": _REVISION,
            "dimension": _DIMENSION,
            "distance": "cosine",
        },
    )
    with pytest.raises(IncompatibleVectorIndexError, match="otro-modelo"):
        await store.ensure_index()
    info = await client.get_collection(store.collection_name)
    metadata = info.config.metadata or {}
    assert metadata["embedding_model"] == "otro-modelo"


def test_repeated_upsert_keeps_the_same_point_count() -> None:
    asyncio.run(_repeats_upsert())


async def _repeats_upsert() -> None:
    batches: list[int] = []
    async with _index(batch_size=2, batches=batches) as (store, _client):
        await store.ensure_index()
        context = _context()
        document_id = uuid4()
        version_id = uuid4()
        records = tuple(
            _record(
                context,
                document_id=document_id,
                version_id=version_id,
                chunk_id=f"fragmento-{number}",
                text=f"texto-{number}",
            )
            for number in range(5)
        )
        await store.upsert(context, records)
        assert await store.count(context) == 5
        await store.ensure_index()
        assert await store.count(context) == 5
        await store.upsert(context, records)
        assert await store.count(context) == 5
        assert batches == [2, 2, 1, 2, 2, 1]


def test_search_cannot_read_another_tenant() -> None:
    asyncio.run(_isolates_tenants())


async def _isolates_tenants() -> None:
    async with _index() as (store, _client):
        await store.ensure_index()
        company_a = _context()
        company_b = _context()
        document_a = uuid4()
        document_b = uuid4()
        version_a = uuid4()
        version_b = uuid4()
        chunk_id = "mismo-fragmento"
        await store.upsert(
            company_a,
            (
                _record(
                    company_a,
                    document_id=document_a,
                    version_id=version_a,
                    chunk_id=chunk_id,
                    text="texto-empresa-a",
                    source="manual.pdf",
                ),
            ),
        )
        await store.upsert(
            company_b,
            (
                _record(
                    company_b,
                    document_id=document_b,
                    version_id=version_b,
                    chunk_id=chunk_id,
                    text="texto-empresa-b",
                    source="manual.pdf",
                ),
            ),
        )
        point_b = point_id(
            tenant_id=company_b.tenant_id,
            document_id=document_b,
            document_version_id=version_b,
            chunk_id=chunk_id,
        )
        point_a = point_id(
            tenant_id=company_a.tenant_id,
            document_id=document_a,
            document_version_id=version_a,
            chunk_id=chunk_id,
        )
        query = VectorQuery(vector=_VECTOR, limit=10)
        visible = await store.search(company_a, query)
        assert {hit.text for hit in visible} == {"texto-empresa-a"}
        by_document = await store.search(
            company_a,
            VectorQuery(vector=_VECTOR, limit=10, document_ids=(document_b,)),
        )
        by_version = await store.search(
            company_a,
            VectorQuery(vector=_VECTOR, limit=10, document_version_ids=(version_b,)),
        )
        by_point = await store.search(
            company_a,
            VectorQuery(vector=_VECTOR, limit=10, point_ids=(point_b,)),
        )
        own_point = await store.search(
            company_a,
            VectorQuery(vector=_VECTOR, limit=10, point_ids=(point_a,)),
        )
        assert by_document == ()
        assert by_version == ()
        assert by_point == ()
        assert [hit.text for hit in own_point] == ["texto-empresa-a"]


def test_search_returns_only_the_active_version() -> None:
    asyncio.run(_activates_one_version())


async def _activates_one_version() -> None:
    async with _index() as (store, _client):
        await store.ensure_index()
        company_a = _context()
        company_b = _context()
        shared_document = uuid4()
        old_version = uuid4()
        new_version = uuid4()
        other_document = uuid4()
        other_version = uuid4()
        foreign_version = uuid4()
        await store.upsert(
            company_a,
            (
                _record(
                    company_a,
                    document_id=shared_document,
                    version_id=old_version,
                    chunk_id="a-vieja",
                    text="version-uno",
                ),
                _record(
                    company_a,
                    document_id=shared_document,
                    version_id=new_version,
                    chunk_id="a-nueva",
                    text="version-dos",
                ),
                _record(
                    company_a,
                    document_id=other_document,
                    version_id=other_version,
                    chunk_id="a-otro",
                    text="sigue-activo",
                ),
            ),
        )
        await store.upsert(
            company_b,
            (
                _record(
                    company_b,
                    document_id=shared_document,
                    version_id=foreign_version,
                    chunk_id="b",
                    text="version-b",
                ),
            ),
        )
        await store.activate_version(
            company_a,
            document_id=shared_document,
            document_version_id=new_version,
        )
        found = await store.search(company_a, VectorQuery(vector=_VECTOR, limit=10))
        assert {hit.text for hit in found} == {"version-dos", "sigue-activo"}
        await store.activate_version(
            company_a,
            document_id=shared_document,
            document_version_id=new_version,
        )
        again = await store.search(company_a, VectorQuery(vector=_VECTOR, limit=10))
        assert {hit.text for hit in again} == {"version-dos", "sigue-activo"}
        foreign = await store.search(company_b, VectorQuery(vector=_VECTOR, limit=10))
        assert {hit.text for hit in foreign} == {"version-b"}
        assert await store.count(company_a) == 3


def test_delete_version_document_and_tenant_remove_only_those_points() -> None:
    asyncio.run(_deletes_scoped_points())


async def _deletes_scoped_points() -> None:
    async with _index() as (store, _client):
        await store.ensure_index()
        company_a = _context()
        company_b = _context()
        shared_document = uuid4()
        old_version = uuid4()
        new_version = uuid4()
        other_document = uuid4()
        other_version = uuid4()
        foreign_version = uuid4()
        await store.upsert(
            company_a,
            (
                _record(
                    company_a,
                    document_id=shared_document,
                    version_id=old_version,
                    chunk_id="a-vieja",
                    text="a-vieja",
                ),
                _record(
                    company_a,
                    document_id=shared_document,
                    version_id=new_version,
                    chunk_id="a-nueva",
                    text="a-nueva",
                ),
                _record(
                    company_a,
                    document_id=other_document,
                    version_id=other_version,
                    chunk_id="a-otro",
                    text="a-otro",
                ),
            ),
        )
        await store.upsert(
            company_b,
            (
                _record(
                    company_b,
                    document_id=shared_document,
                    version_id=foreign_version,
                    chunk_id="b",
                    text="b-compartido",
                ),
            ),
        )
        await store.delete_version(
            company_a,
            document_id=shared_document,
            document_version_id=old_version,
        )
        assert await store.count(company_a) == 2
        assert await store.count(company_b) == 1
        await store.delete_document(company_a, document_id=shared_document)
        assert await store.count(company_a) == 1
        remaining = await store.search(company_a, VectorQuery(vector=_VECTOR, limit=10))
        assert [hit.text for hit in remaining] == ["a-otro"]
        assert await store.count(company_b) == 1
        await store.delete_tenant(company_a)
        assert await store.count(company_a) == 0
        foreign = await store.search(company_b, VectorQuery(vector=_VECTOR, limit=10))
        assert [hit.text for hit in foreign] == ["b-compartido"]
        assert await store.count(company_b) == 1


def test_hits_carry_citation_metadata() -> None:
    asyncio.run(_citation_hit())


async def _citation_hit() -> None:
    async with _index() as (store, _client):
        await store.ensure_index()
        context = _context()
        document_id = uuid4()
        version_id = uuid4()
        record = _record(
            context,
            document_id=document_id,
            version_id=version_id,
            chunk_id="cita-1",
            text="cita-visible",
            source="politica-gastos.md",
            page_start=4,
            page_end=5,
            section="Gastos",
            position=3,
        )
        await store.upsert(context, (record,))
        found = await store.search(context, VectorQuery(vector=_VECTOR, limit=1))
        assert len(found) == 1
        hit = found[0]
        assert hit.point_id == point_id(
            tenant_id=context.tenant_id,
            document_id=document_id,
            document_version_id=version_id,
            chunk_id="cita-1",
        )
        assert hit.source == "politica-gastos.md"
        assert hit.page_start == 4
        assert hit.page_end == 5
        assert hit.section == "Gastos"
        assert hit.position == 3
        assert hit.text == "cita-visible"
        assert hit.parser_version == "parser-v1"
        assert hit.chunker_version == "chunker-v2"
        assert hit.embedding_version == "deterministic-fake-v1"
        assert hit.document_id == document_id
        assert hit.document_version_id == version_id
        assert hit.chunk_id == "cita-1"
        assert hit.is_active is True
        assert hit.tenant_id == context.tenant_id


def test_cleanup_keeps_unrelated_collections() -> None:
    asyncio.run(_keeps_neighbors())


async def _keeps_neighbors() -> None:
    client = create_qdrant_client(get_settings().qdrant_url, timeout_seconds=30)
    try:
        if not await client.collection_exists(_NEIGHBOR):
            await client.create_collection(
                collection_name=_NEIGHBOR,
                vectors_config=VectorParams(size=4, distance=Distance.COSINE),
            )
        probe_before = await client.collection_exists(_PROBE_COLLECTION)
        async with _index() as (store, _raw):
            await store.ensure_index()
            name = store.collection_name
        assert await client.collection_exists(_NEIGHBOR)
        assert not await client.collection_exists(name)
        if probe_before:
            assert await client.collection_exists(_PROBE_COLLECTION)
    finally:
        if await client.collection_exists(_NEIGHBOR):
            await client.delete_collection(_NEIGHBOR)
        await client.close()
