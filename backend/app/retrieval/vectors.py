"""Contrato del índice vectorial. No conoce el SDK de Qdrant."""

import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid5

from app.domain.entities import TenantContext
from app.domain.errors import InvalidStateError

COSINE_DISTANCE = "cosine"
DEFAULT_VECTOR_ALIAS = "engine_rag_active"
DEFAULT_UPSERT_BATCH_SIZE = 64
POINT_NAMESPACE = UUID("b7e1c4a2-6d58-4f0e-9a31-5c8e2d7b4f16")
PAYLOAD_INDEX_FIELDS: tuple[str, ...] = (
    "tenant_id",
    "document_id",
    "document_version_id",
    "chunk_id",
    "is_active",
)

_COLLECTION_PREFIX = "erag_"
_ALIAS_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,62}$")


def require_tenant_context(context: object) -> TenantContext:
    """Rechaza un alcance que no sea el construido por autenticación."""
    if not isinstance(context, TenantContext):
        message = "La operación vectorial exige un TenantContext"
        raise InvalidStateError(message)
    return context


def collection_name(
    *,
    embedding_model: str,
    revision: str,
    dimension: int,
    distance: str = COSINE_DISTANCE,
) -> str:
    """Nombre corto y determinista. El descriptor completo vive en la colección."""
    _require_index_identity(
        embedding_model=embedding_model,
        revision=revision,
        dimension=dimension,
        distance=distance,
    )
    material = f"{embedding_model}|{revision}|{dimension}|{distance}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"{_COLLECTION_PREFIX}{digest}"


def index_descriptor(
    *,
    embedding_model: str,
    revision: str,
    dimension: int,
    distance: str = COSINE_DISTANCE,
) -> dict[str, str | int]:
    """Datos que permiten comprobar que una colección ya creada es la esperada."""
    _require_index_identity(
        embedding_model=embedding_model,
        revision=revision,
        dimension=dimension,
        distance=distance,
    )
    return {
        "embedding_model": embedding_model,
        "revision": revision,
        "dimension": dimension,
        "distance": distance,
    }


def point_id(
    *,
    tenant_id: UUID,
    document_id: UUID,
    document_version_id: UUID,
    chunk_id: str,
) -> UUID:
    """UUID5 estable. El id del fragmento es un hash y Qdrant no lo acepta como punto."""
    if not isinstance(chunk_id, str) or not chunk_id.strip():
        message = "El fragmento no tiene identificador"
        raise InvalidStateError(message)
    material = f"{tenant_id}|{document_id}|{document_version_id}|{chunk_id}"
    return uuid5(POINT_NAMESPACE, material)


def require_alias_name(alias_name: str) -> str:
    """Limita el alias a un identificador corto, distinto de un nombre de colección."""
    if not isinstance(alias_name, str) or _ALIAS_PATTERN.fullmatch(alias_name) is None:
        message = "El alias del índice solo admite letras, números, guion y guion bajo"
        raise InvalidStateError(message)
    return alias_name


@dataclass(frozen=True)
class VectorRecord:
    """Fragmento embedido y metadatos suficientes para citar sin el archivo."""

    tenant_id: UUID
    document_id: UUID
    document_version_id: UUID
    chunk_id: str
    source: str
    page_start: int
    page_end: int
    section: str | None
    position: int
    text: str
    parser_version: str
    chunker_version: str
    embedding_version: str
    vector: tuple[float, ...]
    is_active: bool = True

    def __post_init__(self) -> None:
        _require_text(self.chunk_id, "chunk_id")
        _require_text(self.source, "source")
        _require_text(self.text, "text")
        _require_text(self.parser_version, "parser_version")
        _require_text(self.chunker_version, "chunker_version")
        _require_text(self.embedding_version, "embedding_version")
        if self.section is not None:
            _require_text(self.section, "section")
        if isinstance(self.page_start, bool) or not isinstance(self.page_start, int):
            message = "La página inicial del fragmento no es válida"
            raise InvalidStateError(message)
        if isinstance(self.page_end, bool) or not isinstance(self.page_end, int):
            message = "La página final del fragmento no es válida"
            raise InvalidStateError(message)
        if self.page_start < 1 or self.page_end < self.page_start:
            message = "El rango de páginas del fragmento no es válido"
            raise InvalidStateError(message)
        if (
            isinstance(self.position, bool)
            or not isinstance(self.position, int)
            or self.position < 0
        ):
            message = "La posición del fragmento no es válida"
            raise InvalidStateError(message)
        if not isinstance(self.is_active, bool):
            message = "is_active debe ser booleano"
            raise InvalidStateError(message)
        object.__setattr__(self, "vector", _finite_vector(self.vector))


@dataclass(frozen=True)
class VectorHit:
    """Coincidencia del índice. El texto solo viaja hacia quien va a citarlo."""

    point_id: UUID
    score: float
    tenant_id: UUID
    document_id: UUID
    document_version_id: UUID
    chunk_id: str
    source: str
    page_start: int
    page_end: int
    section: str | None
    position: int
    text: str
    parser_version: str
    chunker_version: str
    embedding_version: str
    is_active: bool


@dataclass(frozen=True)
class VectorQuery:
    """Búsqueda interna. No acepta tenant: sale del contexto autenticado."""

    vector: tuple[float, ...]
    limit: int
    document_ids: tuple[UUID, ...] = ()
    document_version_ids: tuple[UUID, ...] = ()
    point_ids: tuple[UUID, ...] = ()
    section_query: str | None = None
    position_gt: int | None = None
    position_lt: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or self.limit < 1:
            message = "El límite de búsqueda debe ser un entero mayor que cero"
            raise InvalidStateError(message)
        if self.section_query is not None and (
            not isinstance(self.section_query, str) or not self.section_query.strip()
        ):
            message = "La sección buscada no es válida"
            raise InvalidStateError(message)
        _require_position_window(self.position_gt, self.position_lt)
        object.__setattr__(self, "vector", _finite_vector(self.vector))


class VectorStore(Protocol):
    """Puerto de indexación. La implementación vive en infraestructura."""

    async def ensure_index(self) -> None:
        """Crea o valida la colección compartida de esta revisión."""

    async def upsert(self, context: TenantContext, records: Sequence[VectorRecord]) -> None:
        """Inserta o reemplaza puntos de la empresa del contexto."""

    async def activate_version(
        self,
        context: TenantContext,
        *,
        document_id: UUID,
        document_version_id: UUID,
    ) -> None:
        """Marca una versión como la única activa de ese documento."""

    async def delete_version(
        self,
        context: TenantContext,
        *,
        document_id: UUID,
        document_version_id: UUID,
    ) -> None:
        """Borra los puntos de una versión dentro de la empresa del contexto."""

    async def delete_document(self, context: TenantContext, *, document_id: UUID) -> None:
        """Borra los puntos de un documento dentro de la empresa del contexto."""

    async def delete_tenant(self, context: TenantContext) -> None:
        """Borra los puntos de la empresa del contexto."""

    async def search(self, context: TenantContext, query: VectorQuery) -> tuple[VectorHit, ...]:
        """Busca solo puntos activos de la empresa del contexto."""

    async def count(self, context: TenantContext) -> int:
        """Cuenta los puntos de la empresa, activos o no."""


def _require_index_identity(
    *,
    embedding_model: str,
    revision: str,
    dimension: int,
    distance: str,
) -> None:
    _require_label(embedding_model, "modelo")
    _require_label(revision, "revisión")
    if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension < 1:
        message = "La dimensión del índice debe ser un entero mayor que cero"
        raise InvalidStateError(message)
    if distance != COSINE_DISTANCE:
        message = "El índice denso solo admite distancia cosine"
        raise InvalidStateError(message)


def _require_label(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip() or "|" in value:
        message = f"El campo {field} del índice no es válido"
        raise InvalidStateError(message)


def _require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        message = f"El campo {field} del fragmento no es válido"
        raise InvalidStateError(message)


def _require_position_window(lower: int | None, upper: int | None) -> None:
    if lower is None and upper is None:
        return
    if (
        isinstance(lower, bool)
        or isinstance(upper, bool)
        or not isinstance(lower, int)
        or not isinstance(upper, int)
        or lower < 0
        or upper <= lower
    ):
        message = "La ventana de posiciones no es válida"
        raise InvalidStateError(message)


def _finite_vector(values: Sequence[float]) -> tuple[float, ...]:
    if not values:
        message = "El vector no puede estar vacío"
        raise InvalidStateError(message)
    converted = tuple(float(value) for value in values)
    if any(not math.isfinite(value) for value in converted):
        message = "El vector contiene valores no finitos"
        raise InvalidStateError(message)
    return converted
