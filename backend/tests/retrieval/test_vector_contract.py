"""Contrato del índice: nombre, punto y alcance. No abre Qdrant."""

import ast
import asyncio
import hashlib
from typing import cast
from uuid import UUID, uuid4, uuid5

import pytest
from qdrant_client import AsyncQdrantClient

from app.config import repository_root
from app.domain.entities import AuthPrincipal, TenantContext
from app.domain.enums import ApiKeyScope, PrincipalType
from app.domain.errors import InvalidStateError
from app.infrastructure.qdrant_store import QdrantVectorStore
from app.retrieval.errors import DimensionMismatchError
from app.retrieval.vectors import (
    COSINE_DISTANCE,
    DEFAULT_VECTOR_ALIAS,
    POINT_NAMESPACE,
    VectorQuery,
    VectorRecord,
    collection_name,
    point_id,
)

_MODEL = "spec09-fake"
_REVISION = "spec09-test"


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
    vector: tuple[float, ...] = (1.0, 0.0, 0.0, 0.0),
) -> VectorRecord:
    return VectorRecord(
        tenant_id=context.tenant_id,
        document_id=uuid4(),
        document_version_id=uuid4(),
        chunk_id="fragmento",
        source="manual.pdf",
        page_start=1,
        page_end=1,
        section=None,
        position=0,
        text="texto",
        parser_version="parser-v1",
        chunker_version="chunker-v2",
        embedding_version="deterministic-fake-v1",
        vector=vector,
    )


def _store(dimension: int = 4) -> QdrantVectorStore:
    return QdrantVectorStore(
        cast(AsyncQdrantClient, object()),
        embedding_model=_MODEL,
        revision=_REVISION,
        dimension=dimension,
        alias_name="engine_rag_spec09_test",
    )


def test_collection_name_is_a_short_sha256() -> None:
    material = f"{_MODEL}|{_REVISION}|8|{COSINE_DISTANCE}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    name = collection_name(embedding_model=_MODEL, revision=_REVISION, dimension=8)

    assert name == f"erag_{digest}"
    assert name == collection_name(embedding_model=_MODEL, revision=_REVISION, dimension=8)
    assert name != collection_name(embedding_model=_MODEL, revision=_REVISION, dimension=16)
    assert _MODEL not in name
    with pytest.raises(InvalidStateError):
        collection_name(embedding_model=_MODEL, revision=_REVISION, dimension=8, distance="dot")


def test_point_id_is_deterministic_and_includes_the_tenant() -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    document_id = uuid4()
    version_id = uuid4()
    chunk_id = "abc123"
    material = f"{tenant_a}|{document_id}|{version_id}|{chunk_id}"

    identifier = point_id(
        tenant_id=tenant_a,
        document_id=document_id,
        document_version_id=version_id,
        chunk_id=chunk_id,
    )

    assert identifier == uuid5(POINT_NAMESPACE, material)
    assert identifier.version == 5
    assert identifier != point_id(
        tenant_id=tenant_b,
        document_id=document_id,
        document_version_id=version_id,
        chunk_id=chunk_id,
    )


def test_query_cannot_name_a_tenant() -> None:
    assert "tenant_id" not in VectorQuery.__dataclass_fields__
    assert DEFAULT_VECTOR_ALIAS == "engine_rag_active"


def test_vector_contract_does_not_import_qdrant() -> None:
    path = repository_root() / "backend" / "app" / "retrieval" / "vectors.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    assert all(not module.startswith("qdrant") for module in modules)


def test_app_startup_does_not_open_the_vector_index() -> None:
    source = (repository_root() / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    assert "ensure_index" not in source
    assert "QdrantVectorStore" not in source


@pytest.mark.parametrize(
    "action",
    ["upsert", "activate", "delete_version", "delete_document", "delete_tenant", "search", "count"],
)
def test_operations_reject_a_missing_context(action: str) -> None:
    store = _store()
    context = cast(TenantContext, None)
    document_id = uuid4()
    version_id = uuid4()
    query = VectorQuery(vector=(1.0, 0.0, 0.0, 0.0), limit=1)

    async def run() -> None:
        if action == "upsert":
            await store.upsert(context, ())
        elif action == "activate":
            await store.activate_version(
                context,
                document_id=document_id,
                document_version_id=version_id,
            )
        elif action == "delete_version":
            await store.delete_version(
                context,
                document_id=document_id,
                document_version_id=version_id,
            )
        elif action == "delete_document":
            await store.delete_document(context, document_id=document_id)
        elif action == "delete_tenant":
            await store.delete_tenant(context)
        elif action == "search":
            await store.search(context, query)
        else:
            await store.count(context)

    with pytest.raises(InvalidStateError, match="TenantContext"):
        asyncio.run(run())


def test_upsert_rejects_another_tenant_and_a_different_dimension() -> None:
    store = _store()
    context = _context()
    foreign = _record(_context())
    short = _record(context, vector=(1.0, 0.0))

    async def run_foreign() -> None:
        await store.upsert(context, (foreign,))

    async def run_short() -> None:
        await store.upsert(context, (short,))

    with pytest.raises(InvalidStateError, match="empresa"):
        asyncio.run(run_foreign())
    with pytest.raises(DimensionMismatchError, match="espera 4") as caught:
        asyncio.run(run_short())
    assert caught.value.code == "dimension_mismatch"
