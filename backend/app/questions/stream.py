"""Eventos SSE de una respuesta ya validada. No emite tokens parciales."""

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping

from app.questions.models import QuestionFailure

DisconnectCheck = Callable[[], Awaitable[bool]]


def encode_sse(event: str, payload: Mapping[str, object]) -> str:
    """Un evento SSE. El JSON conserva el español."""
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n"


async def iter_question_events(
    body: Mapping[str, object],
    *,
    is_disconnected: DisconnectCheck,
) -> AsyncIterator[str]:
    """Emite status, answer, citations y complete. Se detiene si el cliente se fue."""
    if await is_disconnected():
        return
    request_id = body["request_id"]
    yield encode_sse("status", {"request_id": request_id, "stage": "validated"})
    if await is_disconnected():
        return
    yield encode_sse(
        "answer",
        {
            "request_id": request_id,
            "answer": body["answer"],
            "abstained": body["abstained"],
        },
    )
    if await is_disconnected():
        return
    yield encode_sse(
        "citations",
        {"request_id": request_id, "citations": body["citations"]},
    )
    if await is_disconnected():
        return
    yield encode_sse("complete", dict(body))


async def iter_error_events(
    failure: QuestionFailure,
    *,
    is_disconnected: DisconnectCheck,
) -> AsyncIterator[str]:
    """Un único evento de error, sin el texto que produjo el modelo."""
    if await is_disconnected():
        return
    yield encode_sse(
        "error",
        {
            "request_id": str(failure.request_id),
            "code": failure.code,
            "detail": failure.detail,
        },
    )
