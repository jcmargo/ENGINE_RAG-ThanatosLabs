"""Colección Qdrant compartida. PostgreSQL sigue siendo la fuente de verdad."""

from collections.abc import Mapping, Sequence
from typing import Any, cast
from uuid import UUID

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Condition,
    CreateAlias,
    CreateAliasOperation,
    DeleteAlias,
    DeleteAliasOperation,
    Distance,
    FieldCondition,
    Filter,
    HasIdCondition,
    MatchAny,
    MatchPhrase,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    Range,
    ScoredPoint,
    TextIndexParams,
    TextIndexType,
    TokenizerType,
    VectorParams,
)

from app.domain.entities import TenantContext
from app.domain.errors import InvalidStateError
from app.retrieval.errors import (
    DimensionMismatchError,
    IncompatibleVectorIndexError,
    RetrievalError,
)
from app.retrieval.vectors import (
    COSINE_DISTANCE,
    DEFAULT_UPSERT_BATCH_SIZE,
    DEFAULT_VECTOR_ALIAS,
    PAYLOAD_INDEX_FIELDS,
    VectorHit,
    VectorQuery,
    VectorRecord,
    VectorStore,
    collection_name,
    index_descriptor,
    point_id,
    require_alias_name,
    require_tenant_context,
)

_INDEX_SCHEMAS: dict[str, PayloadSchemaType] = {
    "tenant_id": PayloadSchemaType.KEYWORD,
    "document_id": PayloadSchemaType.KEYWORD,
    "document_version_id": PayloadSchemaType.KEYWORD,
    "chunk_id": PayloadSchemaType.KEYWORD,
    "is_active": PayloadSchemaType.BOOL,
}
_SECTION_TEXT_INDEX = TextIndexParams(
    type=TextIndexType.TEXT,
    tokenizer=TokenizerType.WHITESPACE,
    min_token_len=1,
    max_token_len=32,
    lowercase=True,
    ascii_folding=True,
    phrase_matching=True,
)


class QdrantVectorStore(VectorStore):
    """Proyección densa de una revisión de embeddings. No registra el texto."""

    def __init__(
        self,
        client: AsyncQdrantClient,
        *,
        embedding_model: str,
        revision: str,
        dimension: int,
        alias_name: str = DEFAULT_VECTOR_ALIAS,
        batch_size: int = DEFAULT_UPSERT_BATCH_SIZE,
        distance: str = COSINE_DISTANCE,
    ) -> None:
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
            message = "El tamaño de lote debe ser un entero mayor que cero"
            raise InvalidStateError(message)
        self._client = client
        self._embedding_model = embedding_model
        self._revision = revision
        self._dimension = dimension
        self._distance = distance
        self._alias_name = require_alias_name(alias_name)
        self._batch_size = batch_size
        self._section_index_ready = False
        self._collection_name = collection_name(
            embedding_model=embedding_model,
            revision=revision,
            dimension=dimension,
            distance=distance,
        )

    @property
    def collection_name(self) -> str:
        """Nombre determinista de la colección de esta revisión."""
        return self._collection_name

    async def ensure_index(self) -> None:
        """Crea la colección o la rechaza si su configuración no coincide."""
        if await self._client.collection_exists(self._collection_name):
            await self._assert_compatible()
        else:
            descriptor = index_descriptor(
                embedding_model=self._embedding_model,
                revision=self._revision,
                dimension=self._dimension,
                distance=self._distance,
            )
            metadata: dict[str, Any] = dict(descriptor)
            await self._client.create_collection(
                collection_name=self._collection_name,
                vectors_config=VectorParams(size=self._dimension, distance=Distance.COSINE),
                metadata=metadata,
            )
        await self._ensure_payload_indexes()
        await self._ensure_section_index()
        await self._ensure_alias()

    async def upsert(self, context: TenantContext, records: Sequence[VectorRecord]) -> None:
        """Reemplaza los puntos del lote. Repetir el mismo lote no crea puntos nuevos."""
        tenant = require_tenant_context(context)
        self._require_records(tenant, records)
        points = [self._point(tenant, record) for record in records]
        for start in range(0, len(points), self._batch_size):
            batch = points[start : start + self._batch_size]
            await self._client.upsert(
                collection_name=self._collection_name,
                points=batch,
                wait=True,
            )

    async def activate_version(
        self,
        context: TenantContext,
        *,
        document_id: UUID,
        document_version_id: UUID,
    ) -> None:
        """Activa una versión y desactiva las demás del mismo documento y empresa."""
        tenant = require_tenant_context(context)
        version_filter = Filter(
            must=[
                _match("tenant_id", str(tenant.tenant_id)),
                _match("document_id", str(document_id)),
                _match("document_version_id", str(document_version_id)),
            ]
        )
        others = Filter(
            must=[
                _match("tenant_id", str(tenant.tenant_id)),
                _match("document_id", str(document_id)),
            ],
            must_not=[_match("document_version_id", str(document_version_id))],
        )
        await self._client.set_payload(
            collection_name=self._collection_name,
            payload={"is_active": True},
            points=version_filter,
            wait=True,
        )
        await self._client.set_payload(
            collection_name=self._collection_name,
            payload={"is_active": False},
            points=others,
            wait=True,
        )

    async def delete_version(
        self,
        context: TenantContext,
        *,
        document_id: UUID,
        document_version_id: UUID,
    ) -> None:
        """Borra una versión sin tocar el resto de la empresa ni otras empresas."""
        await self._delete(
            context,
            document_id=document_id,
            document_version_id=document_version_id,
        )

    async def delete_document(self, context: TenantContext, *, document_id: UUID) -> None:
        """Borra un documento sin tocar el resto de la empresa ni otras empresas."""
        await self._delete(context, document_id=document_id)

    async def delete_tenant(self, context: TenantContext) -> None:
        """Borra solo los puntos de la empresa del contexto."""
        await self._delete(context)

    async def search(self, context: TenantContext, query: VectorQuery) -> tuple[VectorHit, ...]:
        """Filtra siempre por la empresa del contexto y por puntos activos."""
        tenant = require_tenant_context(context)
        self._require_dimension(query.vector)
        if query.section_query is not None:
            await self._ensure_section_index()
        response = await self._client.query_points(
            collection_name=self._collection_name,
            query=[float(value) for value in query.vector],
            query_filter=_search_filter(tenant, query),
            limit=query.limit,
            with_payload=True,
        )
        return tuple(self._hit(point, tenant) for point in response.points)

    async def count(self, context: TenantContext) -> int:
        """Cuenta todos los puntos de la empresa, también los inactivos."""
        tenant = require_tenant_context(context)
        result = await self._client.count(
            collection_name=self._collection_name,
            count_filter=Filter(must=[_match("tenant_id", str(tenant.tenant_id))]),
            exact=True,
        )
        return int(result.count)

    async def _assert_compatible(self) -> None:
        info = await self._client.get_collection(self._collection_name)
        vectors = info.config.params.vectors
        metadata = _metadata(info.config.metadata)
        expected = index_descriptor(
            embedding_model=self._embedding_model,
            revision=self._revision,
            dimension=self._dimension,
            distance=self._distance,
        )
        compatible = (
            isinstance(vectors, VectorParams)
            and vectors.size == self._dimension
            and vectors.distance == Distance.COSINE
            and _metadata_matches(metadata, expected)
        )
        if compatible:
            return
        actual_dimension: object = vectors.size if isinstance(vectors, VectorParams) else "ausente"
        actual_distance: object = (
            vectors.distance.value if isinstance(vectors, VectorParams) else "ausente"
        )
        message = (
            f"La colección {self._collection_name} no es compatible. "
            f"Se esperaba dimensión {self._dimension}, distancia {self._distance}, "
            f"modelo {self._embedding_model!r} y revisión {self._revision!r}. "
            f"Hay dimensión {actual_dimension}, distancia {actual_distance}, "
            f"modelo {metadata.get('embedding_model')!r} y revisión {metadata.get('revision')!r}."
        )
        raise IncompatibleVectorIndexError(message)

    async def _ensure_payload_indexes(self) -> None:
        info = await self._client.get_collection(self._collection_name)
        existing = set(info.payload_schema)
        for field_name in PAYLOAD_INDEX_FIELDS:
            if field_name in existing:
                continue
            await self._client.create_payload_index(
                collection_name=self._collection_name,
                field_name=field_name,
                field_schema=_INDEX_SCHEMAS[field_name],
                wait=True,
            )

    async def _ensure_section_index(self) -> None:
        """Índice de frase para «Teorema 7.4.4». El coseno no distingue un rótulo vacío."""
        if self._section_index_ready:
            return
        info = await self._client.get_collection(self._collection_name)
        if "section" not in info.payload_schema:
            await self._client.create_payload_index(
                collection_name=self._collection_name,
                field_name="section",
                field_schema=_SECTION_TEXT_INDEX,
                wait=True,
            )
        self._section_index_ready = True

    async def _ensure_alias(self) -> None:
        aliases = await self._client.get_aliases()
        current = next(
            (item for item in aliases.aliases if item.alias_name == self._alias_name),
            None,
        )
        if current is not None and current.collection_name == self._collection_name:
            return
        operations: list[CreateAliasOperation | DeleteAliasOperation] = []
        if current is not None:
            operations.append(
                DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=self._alias_name))
            )
        operations.append(
            CreateAliasOperation(
                create_alias=CreateAlias(
                    collection_name=self._collection_name,
                    alias_name=self._alias_name,
                )
            )
        )
        await self._client.update_collection_aliases(change_aliases_operations=operations)

    def _require_records(self, context: TenantContext, records: Sequence[VectorRecord]) -> None:
        for record in records:
            if record.tenant_id != context.tenant_id:
                message = "El fragmento no pertenece a la empresa del contexto"
                raise InvalidStateError(message)
            self._require_dimension(record.vector)

    def _require_dimension(self, vector: Sequence[float]) -> None:
        if len(vector) != self._dimension:
            message = (
                f"La dimensión del vector es {len(vector)} y el índice espera {self._dimension}"
            )
            raise DimensionMismatchError(message)

    def _point(self, context: TenantContext, record: VectorRecord) -> PointStruct:
        payload: dict[str, Any] = {
            "tenant_id": str(context.tenant_id),
            "document_id": str(record.document_id),
            "document_version_id": str(record.document_version_id),
            "chunk_id": record.chunk_id,
            "source": record.source,
            "page_start": record.page_start,
            "page_end": record.page_end,
            "position": record.position,
            "text": record.text,
            "parser_version": record.parser_version,
            "chunker_version": record.chunker_version,
            "embedding_version": record.embedding_version,
            "is_active": record.is_active,
        }
        if record.section is not None:
            payload["section"] = record.section
        return PointStruct(
            id=point_id(
                tenant_id=context.tenant_id,
                document_id=record.document_id,
                document_version_id=record.document_version_id,
                chunk_id=record.chunk_id,
            ),
            vector=[float(value) for value in record.vector],
            payload=payload,
        )

    def _hit(self, point: ScoredPoint, context: TenantContext) -> VectorHit:
        payload = _metadata(point.payload)
        tenant_id = _uuid_field(payload, "tenant_id")
        if tenant_id != context.tenant_id:
            message = "El índice devolvió un punto de otra empresa"
            raise InvalidStateError(message)
        if isinstance(point.score, bool) or not isinstance(point.score, int | float):
            message = "El punto no trae puntuación"
            raise RetrievalError(message)
        return VectorHit(
            point_id=_point_uuid(point.id),
            score=float(point.score),
            tenant_id=tenant_id,
            document_id=_uuid_field(payload, "document_id"),
            document_version_id=_uuid_field(payload, "document_version_id"),
            chunk_id=_text_field(payload, "chunk_id"),
            source=_text_field(payload, "source"),
            page_start=_int_field(payload, "page_start"),
            page_end=_int_field(payload, "page_end"),
            section=_section(payload),
            position=_int_field(payload, "position"),
            text=_text_field(payload, "text"),
            parser_version=_text_field(payload, "parser_version"),
            chunker_version=_text_field(payload, "chunker_version"),
            embedding_version=_text_field(payload, "embedding_version"),
            is_active=_active(payload),
        )

    async def _delete(
        self,
        context: TenantContext,
        *,
        document_id: UUID | None = None,
        document_version_id: UUID | None = None,
    ) -> None:
        tenant = require_tenant_context(context)
        must: list[Condition] = [_match("tenant_id", str(tenant.tenant_id))]
        if document_id is not None:
            must.append(_match("document_id", str(document_id)))
        if document_version_id is not None:
            must.append(_match("document_version_id", str(document_version_id)))
        await self._client.delete(
            collection_name=self._collection_name,
            points_selector=Filter(must=must),
            wait=True,
        )


def _match(key: str, value: str | bool) -> FieldCondition:
    return FieldCondition(key=key, match=MatchValue(value=value))


def _search_filter(context: TenantContext, query: VectorQuery) -> Filter:
    must: list[Condition] = [
        _match("tenant_id", str(context.tenant_id)),
        _match("is_active", True),
    ]
    if query.document_ids:
        must.append(_any("document_id", query.document_ids))
    if query.document_version_ids:
        must.append(_any("document_version_id", query.document_version_ids))
    if query.point_ids:
        must.append(HasIdCondition(has_id=list(query.point_ids)))
    if query.section_query is not None:
        must.append(FieldCondition(key="section", match=MatchPhrase(phrase=query.section_query)))
    if query.position_gt is not None and query.position_lt is not None:
        must.append(
            FieldCondition(
                key="position",
                range=Range(gt=query.position_gt, lt=query.position_lt),
            )
        )
    return Filter(must=must)


def _any(key: str, values: Sequence[UUID]) -> FieldCondition:
    return FieldCondition(key=key, match=MatchAny(any=[str(value) for value in values]))


def _metadata(value: object) -> Mapping[str, object]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    message = "Qdrant devolvió metadatos ilegibles"
    raise RetrievalError(message)


def _metadata_matches(actual: Mapping[str, object], expected: Mapping[str, str | int]) -> bool:
    for key, value in expected.items():
        found = actual.get(key)
        if isinstance(value, int) and (found == value or found == str(value)):
            continue
        if found != value:
            return False
    return True


def _uuid_field(payload: Mapping[str, object], key: str) -> UUID:
    value = payload.get(key)
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError as exc:
            message = f"El punto no trae un {key} válido"
            raise RetrievalError(message) from exc
    message = f"El punto no trae un {key} válido"
    raise RetrievalError(message)


def _point_uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError as exc:
            message = "El punto no trae un id válido"
            raise RetrievalError(message) from exc
    message = "El punto no trae un id válido"
    raise RetrievalError(message)


def _text_field(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value
    message = f"El punto no trae el campo {key}"
    raise RetrievalError(message)


def _int_field(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        message = f"El punto no trae un {key} válido"
        raise RetrievalError(message)
    return value


def _section(payload: Mapping[str, object]) -> str | None:
    value = payload.get("section")
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value
    message = "El punto no trae una sección válida"
    raise RetrievalError(message)


def _active(payload: Mapping[str, object]) -> bool:
    value = payload.get("is_active")
    if isinstance(value, bool):
        return value
    message = "El punto no trae is_active"
    raise RetrievalError(message)
