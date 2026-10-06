"""Límites de apertura de PDF/DOCX antes de expandir el contenido."""

from __future__ import annotations

import zipfile

# Tope de subida comprimida del producto (config por defecto: 25 MiB).
_UPLOAD_LIMIT_BYTES = 25 * 1024 * 1024
# 4x el tope de subida: un DOCX legítimo raramente expande más; un ZIP hostil
# bajo 25 MiB comprimidos no puede declarar gigabytes y forzar la reserva.
MAX_UNCOMPRESSED_BYTES = 4 * _UPLOAD_LIMIT_BYTES
# Tope de páginas al validar con PyMuPDF en la subida (DoS, no cupo de negocio).
MAX_PDF_PAGES = 10_000

_OVERSIZE = "El archivo descomprimido supera el límite permitido"
_TOO_MANY_PAGES = "El PDF supera el número máximo de páginas permitido"


class UncompressedSizeError(Exception):
    """El ZIP declara un tamaño descomprimido por encima del tope."""

    def __init__(self, message: str = _OVERSIZE) -> None:
        super().__init__(message)
        self.message = message


def assert_zip_uncompressed_within_limit(
    archive: zipfile.ZipFile,
    *,
    max_uncompressed_bytes: int | None = None,
) -> None:
    """Rechaza por `ZipInfo.file_size` sin leer ni descomprimir entradas."""
    limit = MAX_UNCOMPRESSED_BYTES if max_uncompressed_bytes is None else max_uncompressed_bytes
    total = 0
    for info in archive.infolist():
        declared = info.file_size
        if declared > limit:
            raise UncompressedSizeError(_OVERSIZE)
        total += declared
        if total > limit:
            raise UncompressedSizeError(_OVERSIZE)


def too_many_pdf_pages_message() -> str:
    """Mensaje estable cuando el PDF declara demasiadas páginas."""
    return _TOO_MANY_PAGES
