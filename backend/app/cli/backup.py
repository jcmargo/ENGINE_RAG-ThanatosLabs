"""Captura cifrada: `python -m app.cli.backup`."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from app.backup.wiring import backup_coordinator
from app.config import get_settings
from app.infrastructure.database import create_database_engine, create_session_factory
from app.observability.logging import configure_logging


def main() -> int:
    """Ejecuta un backup y escribe el checksum en la salida, no los secretos."""
    settings = get_settings()
    configure_logging(
        settings.log_level.value,
        service="engine-rag-backup",
        environment=settings.environment.value,
    )
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    staging = Path(tempfile.mkdtemp(prefix="engine-rag-backup-"))
    try:
        result = asyncio.run(backup_coordinator(settings, factory).run(staging))
    finally:
        asyncio.run(engine.dispose())
    print(f"backup sha256={result.manifest.digest()} tenants={result.manifest.counts['tenants']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
