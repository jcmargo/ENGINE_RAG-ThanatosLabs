"""Purga por lotes: `python -m app.cli.retention --dry-run`."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from app.backup.wiring import export_store
from app.config import get_settings
from app.infrastructure.database import create_database_engine, create_session_factory
from app.observability.logging import configure_logging
from app.retention.service import RetentionService


def main(argv: Sequence[str] | None = None) -> int:
    """Purga filas vencidas. `--dry-run` solo cuenta."""
    parser = argparse.ArgumentParser(prog="app.cli.retention")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    settings = get_settings()
    configure_logging(
        settings.log_level.value,
        service="engine-rag-retention",
        environment=settings.environment.value,
    )
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    service = RetentionService(
        factory,
        export_store(settings),
        conversation_days=settings.conversation_retention_days,
        audit_days=settings.audit_retention_days,
        batch_size=settings.retention_batch_size,
    )
    try:
        while True:
            outcome = asyncio.run(service.purge_once(dry_run=bool(args.dry_run)))
            print(
                "retencion "
                f"dry_run={outcome.dry_run} conversations={outcome.conversations} "
                f"traces={outcome.traces} audit={outcome.audit} exports={outcome.exports}"
            )
            if args.dry_run or not outcome.more:
                break
    finally:
        asyncio.run(engine.dispose())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
