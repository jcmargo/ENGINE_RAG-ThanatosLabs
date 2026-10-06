"""Restaura un snapshot: `python -m app.cli.restore --snapshot latest`."""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from app.backup.wiring import restore_coordinator
from app.config import get_settings
from app.infrastructure.database import create_database_engine, create_session_factory
from app.lifecycle.rebuild import enqueue_ready_reindex
from app.observability.logging import configure_logging


def main(argv: Sequence[str] | None = None) -> int:
    """Restaura y reconcilia tombstones antes de declarar el tráfico abierto."""
    parser = argparse.ArgumentParser(prog="app.cli.restore")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--skip-qdrant", action="store_true")
    parser.add_argument("--rebuild-vectors", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    settings = get_settings()
    configure_logging(
        settings.log_level.value,
        service="engine-rag-restore",
        environment=settings.environment.value,
    )
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    staging = Path(tempfile.mkdtemp(prefix="engine-rag-restore-"))
    try:
        result = asyncio.run(
            restore_coordinator(settings, factory).run(
                staging,
                snapshot_id=str(args.snapshot),
                skip_qdrant=bool(args.skip_qdrant),
            )
        )
        enqueued = 0
        if args.rebuild_vectors or not result.qdrant_restored:
            enqueued = asyncio.run(
                enqueue_ready_reindex(
                    factory,
                    now=datetime.now(UTC),
                    max_attempts=settings.worker_max_attempts,
                )
            )
    finally:
        asyncio.run(engine.dispose())
    print(
        "restore listo "
        f"snapshot={result.snapshot_id} tombstones={result.tombstones_applied} "
        f"qdrant={result.qdrant_restored} reindex={enqueued}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
