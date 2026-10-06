"""Rutas de preguntas. El tenant sale de `TenantContext` y el router solo delega."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Coroutine
from contextvars import Token
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.deps import require_query_context
from app.api.history import get_conversation_service
from app.api.question_schemas import (
    DebugEvidenceResponse,
    ModelListResponse,
    ModelOptionResponse,
    QuestionDebugResponse,
    QuestionRequestBody,
    QuestionResponse,
)
from app.conversations.service import ConversationService
from app.domain.entities import TenantContext
from app.domain.enums import PrincipalType
from app.domain.errors import ConflictError, InvalidStateError, NotFoundError
from app.domain.history import HISTORY_SCOPE
from app.generation.directory import (
    LlmChoices,
    bind_llm_choice,
    reset_llm_choice,
)
from app.generation.errors import GenerationError
from app.observability.context import current_request_id
from app.questions.models import (
    PublicCitation,
    QuestionAnswer,
    QuestionDebug,
    QuestionFailure,
    QuestionRequest,
)
from app.questions.service import QuestionService
from app.questions.stream import iter_error_events, iter_question_events
from app.retrieval.contracts import DEFAULT_TOP_K

router = APIRouter(tags=["questions"])
logger = logging.getLogger(__name__)

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
_GENERATION_DETAILS = {
    "llm_timeout": "El modelo no respondió a tiempo",
    "llm_unavailable": "El modelo no está disponible",
    "llm_response_error": "El modelo no pudo completar la respuesta",
    "generation_format_error": "No se pudo producir una respuesta válida",
    "ungrounded_answer": "No se pudo producir una respuesta válida",
    "generation_error": "No se pudo producir una respuesta válida",
    "streaming_not_available": "No se pudo producir una respuesta válida",
}
_INTERNAL_DETAIL = "No se pudo completar la pregunta"


def _observe_detached(task: asyncio.Task[QuestionAnswer]) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.info("pregunta status=detached_error error_type=%s", type(error).__name__)


async def _await_saved(work: Coroutine[object, object, QuestionAnswer]) -> QuestionAnswer:
    """Termina y guarda el turno aunque el cliente cierre la conexión."""
    task = asyncio.create_task(work)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        task.add_done_callback(_observe_detached)
        raise


def get_question_service(request: Request) -> QuestionService:
    """Servicio de preguntas. Se construye en el primer uso si no hay uno inyectado."""
    current = getattr(request.app.state, "question_service", None)
    if isinstance(current, QuestionService):
        return current
    from app.generation.directory import ConfiguredLlmDirectory
    from app.questions.runtime import build_question_service

    directory = getattr(request.app.state, "llm_directory", None)
    if not isinstance(directory, ConfiguredLlmDirectory):
        directory = ConfiguredLlmDirectory(request.app.state.settings)
        request.app.state.llm_directory = directory
    service = build_question_service(
        request.app.state.settings,
        request.app.state.session_factory,
        request.app.state.qdrant_client,
        directory,
    )
    request.app.state.question_service = service
    return service


@router.get("/v1/models", response_model=ModelListResponse)
async def list_models(
    request: Request,
    context: Annotated[TenantContext, Depends(require_query_context)],
) -> ModelListResponse | JSONResponse:
    """Lista los modelos de Ollama y, si está configurada, la API externa."""
    del context
    try:
        options = await asyncio.to_thread(_llm_choices(request).list_models)
    except GenerationError as error:
        failure = _generation_failure(current_request_id(), error)
        return JSONResponse(status_code=failure.status_code, content=_failure_body(failure))
    return ModelListResponse(
        items=[
            ModelOptionResponse(id=item.id, label=item.label, provider=item.provider)
            for item in options
        ]
    )


@router.post("/v1/questions", response_model=QuestionResponse)
async def ask_question(
    body: QuestionRequestBody,
    request: Request,
    context: Annotated[TenantContext, Depends(require_query_context)],
    history: Annotated[ConversationService, Depends(get_conversation_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    debug: Annotated[bool, Query()] = False,
) -> JSONResponse:
    """Responde con citas o abstención. Un seguimiento usa la pregunta anterior solo para buscar."""
    request_id = current_request_id()
    try:
        token = _bind_choice(body, request)
        try:
            result = await _await_saved(
                history.ask(
                    context,
                    _request(body, context, request_id, debug),
                    idempotency_key=idempotency_key,
                )
            )
        finally:
            reset_llm_choice(token)
    except (NotFoundError, ConflictError):
        raise
    except InvalidStateError as error:
        return _invalid_input(request_id, error)
    except GenerationError as error:
        failure = _generation_failure(request_id, error)
        return JSONResponse(status_code=failure.status_code, content=_failure_body(failure))
    except Exception as error:
        _log_internal(request_id, error)
        failure = _internal_failure(request_id)
        return JSONResponse(status_code=failure.status_code, content=_failure_body(failure))
    return JSONResponse(status_code=200, content=_public_body(result))


@router.post("/v1/questions/stream", response_model=None)
async def stream_question(
    body: QuestionRequestBody,
    request: Request,
    context: Annotated[TenantContext, Depends(require_query_context)],
    history: Annotated[ConversationService, Depends(get_conversation_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    debug: Annotated[bool, Query()] = False,
) -> JSONResponse | StreamingResponse:
    """Emite el mismo resultado validado que `POST /v1/questions`."""
    request_id = current_request_id()
    try:
        token = _bind_choice(body, request)
        try:
            result = await _await_saved(
                history.ask(
                    context,
                    _request(body, context, request_id, debug),
                    idempotency_key=idempotency_key,
                )
            )
        finally:
            reset_llm_choice(token)
    except (NotFoundError, ConflictError):
        raise
    except InvalidStateError as error:
        return _invalid_input(request_id, error)
    except GenerationError as error:
        failure = _generation_failure(request_id, error)
        return _stream_failure(failure, request)
    except Exception as error:
        _log_internal(request_id, error)
        return _stream_failure(_internal_failure(request_id), request)
    return StreamingResponse(
        _events(_public_body(result), request),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


def _llm_choices(request: Request) -> LlmChoices:
    current = getattr(request.app.state, "llm_directory", None)
    if isinstance(current, LlmChoices):
        return current
    from app.generation.directory import ConfiguredLlmDirectory

    directory = ConfiguredLlmDirectory(request.app.state.settings)
    request.app.state.llm_directory = directory
    return directory


def _bind_choice(body: QuestionRequestBody, request: Request) -> Token[str | None]:
    directory = getattr(request.app.state, "llm_directory", None)
    if isinstance(directory, LlmChoices):
        directory.ensure(body.llm)
    return bind_llm_choice(body.llm)


def _request(
    body: QuestionRequestBody,
    context: TenantContext,
    request_id: UUID,
    debug: bool,
) -> QuestionRequest:
    document_ids = () if body.document_ids is None else tuple(body.document_ids)
    return QuestionRequest(
        question=body.question,
        top_k=DEFAULT_TOP_K if body.top_k is None else body.top_k,
        document_ids=document_ids,
        request_id=request_id,
        include_debug=debug and context.principal.type is PrincipalType.TENANT_ADMIN,
        conversation_id=body.conversation_id,
        llm=body.llm,
    )


def _invalid_input(request_id: UUID, error: InvalidStateError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "code": "invalid_question",
            "detail": error.message,
            "request_id": str(request_id),
        },
    )


def _generation_failure(request_id: UUID, error: GenerationError) -> QuestionFailure:
    return QuestionFailure(
        request_id=request_id,
        status_code=503,
        code=error.code,
        detail=_GENERATION_DETAILS.get(error.code, _GENERATION_DETAILS["generation_error"]),
    )


def _internal_failure(request_id: UUID) -> QuestionFailure:
    return QuestionFailure(
        request_id=request_id,
        status_code=500,
        code="internal_error",
        detail=_INTERNAL_DETAIL,
    )


def _failure_body(failure: QuestionFailure) -> dict[str, str]:
    return {
        "code": failure.code,
        "detail": failure.detail,
        "request_id": str(failure.request_id),
    }


def _log_internal(request_id: UUID, error: Exception) -> None:
    logger.info(
        "pregunta request_id=%s status=error code=internal_error error_type=%s",
        request_id,
        type(error).__name__,
    )


def _public_body(result: QuestionAnswer) -> dict[str, object]:
    payload: dict[str, object] = {
        "request_id": str(result.request_id),
        "answer": result.answer,
        "abstained": result.abstained,
        "citations": [_citation_body(item) for item in result.citations],
        "pipeline_version": result.pipeline_version,
    }
    if result.conversation_id is not None and result.message_id is not None:
        payload["conversation_id"] = str(result.conversation_id)
        payload["message_id"] = str(result.message_id)
        payload["history_scope"] = result.history_scope or HISTORY_SCOPE
    if result.debug is not None:
        payload["debug"] = _debug_body(result.debug)
    return payload


def _citation_body(item: PublicCitation) -> dict[str, object]:
    return {
        "document_id": str(item.document_id),
        "document_name": item.document_name,
        "page": item.page,
        "section": item.section,
        "excerpt": item.excerpt,
    }


def _debug_body(debug: QuestionDebug) -> dict[str, object]:
    return QuestionDebugResponse(
        request_id=debug.request_id,
        embedding_version=debug.embedding_version,
        index_version=debug.index_version,
        hits_retrieved=debug.hits_retrieved,
        hits_after_validation=debug.hits_after_validation,
        hits_after_threshold=debug.hits_after_threshold,
        returned=debug.returned,
        rounds=debug.rounds,
        top_k=debug.top_k,
        min_retrieval_score=debug.min_retrieval_score,
        embed_ms=debug.embed_ms,
        search_ms=debug.search_ms,
        validate_ms=debug.validate_ms,
        generation_ms=debug.generation_ms,
        llm_called=debug.llm_called,
        token_estimate=debug.token_estimate,
        evidence=[
            DebugEvidenceResponse(
                chunk_id=item.chunk_id,
                document_id=item.document_id,
                score=item.score,
                position=item.position,
            )
            for item in debug.evidence
        ],
    ).model_dump(mode="json")


async def _events(body: dict[str, object], request: Request) -> AsyncIterator[str]:
    async for event in iter_question_events(body, is_disconnected=request.is_disconnected):
        yield event


def _stream_failure(failure: QuestionFailure, request: Request) -> StreamingResponse:
    return StreamingResponse(
        iter_error_events(failure, is_disconnected=request.is_disconnected),
        status_code=failure.status_code,
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
