"""Reconstruye vectores: `python -m app.cli.rebuild_vectors`."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.config import get_settings
from app.infrastructure.database import create_database_engine, create_session_factory
from app.lifecycle.rebuild import enqueue_ready_reindex
from app.observability.logging import configure_logging


def main() -> int:
    """Encola un reindex por cada versión ready. El worker hace el resto."""
    settings = get_settings()
    configure_logging(
        settings.log_level.value,
        service="engine-rag-rebuild",
        environment=settings.environment.value,
    )
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    try:
        created = asyncio.run(
            enqueue_ready_reindex(
                factory,
                now=datetime.now(UTC),
                max_attempts=settings.worker_max_attempts,
            )
        )
    finally:
        asyncio.run(engine.dispose())
    print(f"rebuild enqueued={created}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
