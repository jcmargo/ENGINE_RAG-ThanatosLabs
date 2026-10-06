"""Selección de archivos de almacenamiento sin fila en `document_versions`.

La ventana promote→commit de una carga puede dejar un archivo en disco antes de
que exista la fila. El periodo de gracia evita borrarlo mientras la subida sigue
en curso; debe superar el lease del worker (60s por defecto).
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass
from datetime import datetime, timedelta

# Varios minutos: cubre el lease del worker y commits lentos o pausas del GC.
ORPHAN_GRACE = timedelta(minutes=5)


@dataclass(frozen=True)
class OrphanSweepPlan:
    """Claves y nombres de staging a eliminar en una pasada de barrido."""

    storage_keys: tuple[str, ...]
    staged_names: tuple[str, ...]

    @property
    def total(self) -> int:
        return len(self.storage_keys) + len(self.staged_names)


def plan_orphan_deletions(
    *,
    stored_mtimes: Mapping[str, datetime],
    staged_mtimes: Mapping[str, datetime],
    referenced_keys: Set[str],
    now: datetime,
    grace: timedelta = ORPHAN_GRACE,
) -> OrphanSweepPlan:
    """Devuelve objetos no referenciados cuya mtime está fuera del periodo de gracia.

    Cualquier clave presente en `referenced_keys` se conserva, sin importar el
    estado de la versión (queued, failed, deleting, etc.): el worker borra el
    archivo cuando termina la eliminación.
    """
    cutoff = now - grace
    storage_keys = tuple(
        sorted(
            key
            for key, mtime in stored_mtimes.items()
            if key not in referenced_keys and mtime <= cutoff
        )
    )
    staged_names = tuple(sorted(name for name, mtime in staged_mtimes.items() if mtime <= cutoff))
    return OrphanSweepPlan(storage_keys=storage_keys, staged_names=staged_names)
