"""Modelos inmutables de extracción y chunking."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractedPage:
    """Página con numeración desde 1 y sección opcional."""

    number: int
    text: str
    section: str | None = None

    def __post_init__(self) -> None:
        if self.number < 1:
            message = "El número de página empieza en 1"
            raise ValueError(message)


@dataclass(frozen=True)
class ExtractedDocument:
    """Documento extraído, con páginas y versión del parser."""

    name: str
    mime_type: str
    pages: tuple[ExtractedPage, ...]
    parser_version: str


@dataclass(frozen=True)
class Chunk:
    """Fragmento citable. No mezcla páginas."""

    id: str
    text: str
    page_start: int
    page_end: int
    section: str | None
    position: int
    chunker_version: str

    def __post_init__(self) -> None:
        if self.page_start < 1 or self.page_end < self.page_start:
            message = "El rango de páginas del chunk no es válido"
            raise ValueError(message)
        if self.position < 0:
            message = "La posición del chunk no puede ser negativa"
            raise ValueError(message)
