"""Una desconexión no cancela el turno que todavía se está guardando."""

import asyncio

from app.api.questions import _await_saved


def test_client_disconnect_lets_the_turn_finish() -> None:
    asyncio.run(_run())


async def _run() -> None:
    started = asyncio.Event()
    finished = asyncio.Event()

    async def work() -> str:
        started.set()
        await asyncio.sleep(0.05)
        finished.set()
        return "saved"

    waiter = asyncio.create_task(_await_saved(work()))
    await started.wait()
    waiter.cancel()
    try:
        await waiter
    except asyncio.CancelledError:
        pass
    else:
        message = "La espera del cliente debía cancelarse"
        raise AssertionError(message)

    await asyncio.wait_for(finished.wait(), timeout=1)
