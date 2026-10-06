"""Nombre visible de un archivo, sin usar la ruta enviada por el cliente."""

from pathlib import PurePosixPath

from app.domain.errors import InvalidStateError

_MAX_NAME_LENGTH = 255


def sanitize_display_name(raw: str) -> str:
    """Deja solo el último segmento y rechaza un nombre vacío."""
    cleaned = raw.replace("\\", "/").replace("\x00", "")
    name = PurePosixPath(cleaned).name.strip()
    if not name or name in {".", ".."} or len(name) > _MAX_NAME_LENGTH:
        message = "El nombre del archivo no es válido"
        raise InvalidStateError(message)
    return name
