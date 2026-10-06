"""Arranque del worker: `python -m app.worker` y `python -m app.worker reconcile`."""

import argparse
import asyncio
import signal
import types
from collections.abc import Callable, Sequence

from app.config import get_settings
from app.worker.runtime import IngestionWorker, build_worker


def main(argv: Sequence[str] | None = None) -> int:
    """Ejecuta el bucle o una pasada de reconciliación y sale."""
    command = parse_command(argv)
    settings = get_settings()
    from app.observability.logging import configure_logging

    configure_logging(
        settings.log_level.value,
        service="engine-rag-worker",
        environment=settings.environment.value,
    )
    worker = build_worker(settings)
    if command == "reconcile":
        asyncio.run(_reconcile(worker))
    else:
        asyncio.run(_run(worker))
    return 0


def parse_command(argv: Sequence[str] | None = None) -> str:
    """Acepta `run` por omisión y `reconcile` como pasada única."""
    parser = argparse.ArgumentParser(prog="app.worker")
    parser.add_argument("command", nargs="?", choices=("run", "reconcile"), default="run")
    args = parser.parse_args(list(argv) if argv is not None else None)
    command = args.command
    assert isinstance(command, str)
    return command


def _install_threadsafe(
    loop: asyncio.AbstractEventLoop,
    signum: int,
    request_stop: Callable[[], None],
) -> None:
    def handler(_signum: int, _frame: types.FrameType | None) -> None:
        loop.call_soon_threadsafe(request_stop)

    try:
        signal.signal(signum, handler)
    except (ValueError, OSError):
        return


def install_stop(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    """SIGINT y SIGTERM dejan de reclamar. En Windows el aviso vuelve al hilo del loop."""

    def request_stop() -> None:
        stop.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, request_stop)
        except (NotImplementedError, RuntimeError):
            _install_threadsafe(loop, signum, request_stop)


async def _run(worker: IngestionWorker) -> None:
    stop = asyncio.Event()
    install_stop(asyncio.get_running_loop(), stop)
    try:
        await worker.run(stop)
    finally:
        await worker.aclose()


async def _reconcile(worker: IngestionWorker) -> None:
    try:
        await worker.run_available()
    finally:
        await worker.aclose()


if __name__ == "__main__":
    raise SystemExit(main())
