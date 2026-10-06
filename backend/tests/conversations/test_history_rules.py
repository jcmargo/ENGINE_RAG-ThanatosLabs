"""Reglas del historial sin PostgreSQL.

La búsqueda de un seguimiento usa la pregunta de usuario anterior. El modelo,
el mensaje guardado y la idempotencia siguen el texto nuevo.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.api.conversations_mapping import daily_response
from app.conversations.payload import canonical_payload_hash, hash_key, owner_from
from app.conversations.service import ConversationService
from app.domain.entities import AuthPrincipal, TenantContext
from app.domain.enums import ApiKeyScope, FeedbackRating, MessageRole, PrincipalType, TenantRole
from app.domain.errors import ConflictError, NotFoundError
from app.generation.fake import FakeLLMProvider
from app.generation.models import PUBLIC_ABSTENTION
from app.questions.models import QuestionAnswer, QuestionDebug, QuestionRequest
from app.questions.service import QuestionService
from app.retrieval.contracts import Evidence, RetrievalRequest, RetrievalResult, RetrievalTrace
from tests.conversations.memory import MemoryHistoryStore

_PLANTED_QUESTION = "PREGUNTA-PLANTA sobre vacaciones"
_PLANTED_ANSWER = "RESPUESTA-PLANTA 18 días"
_THEOREM = "que dice el teorema 8.4.4"
_FOLLOW_UP = "dime la demostracion"
_EXAMPLE = "y un ejemplo"
_PREVIOUS_ANSWER = "ENUNCIADO-PREVIO"
_NEXT_ANSWER = "EJEMPLO-SIGUIENTE"
_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current


class _CountingQuestions:
    def __init__(self) -> None:
        self.seen: list[str] = []

    async def answer(
        self,
        context: TenantContext,
        request: QuestionRequest,
        *,
        retrieval_query: str | None = None,
        part: str | None = None,
        topics: tuple[tuple[str, str], ...] = (),
    ) -> QuestionAnswer:
        del context, retrieval_query, part, topics
        self.seen.append(request.question)
        request_id = request.request_id if request.request_id is not None else uuid4()
        return QuestionAnswer(
            request_id=request_id,
            answer=_PLANTED_ANSWER,
            abstained=False,
            citations=(),
            pipeline_version="embed|v1|prompt-v4|fake",
            technical=_technical(request_id),
        )


class _Retrieval:
    def __init__(self, evidence: tuple[Evidence, ...] = ()) -> None:
        self.requests: list[RetrievalRequest] = []
        self.containing: list[tuple[str, ...]] = []
        self.parts: list[tuple[str, tuple[tuple[str, str], ...]]] = []
        self._evidence = evidence

    async def retrieve(
        self,
        context: TenantContext,
        documents: object,
        request: RetrievalRequest,
        *,
        candidate_floor: float | None = None,
        containing: tuple[str, ...] = (),
    ) -> RetrievalResult:
        del context, documents, candidate_floor
        self.requests.append(request)
        self.containing.append(containing)
        found = len(self._evidence)
        return self._result(request, found)

    async def retrieve_part(
        self,
        context: TenantContext,
        documents: object,
        request: RetrievalRequest,
        *,
        part: str,
        topics: tuple[tuple[str, str], ...],
    ) -> RetrievalResult:
        del context, documents
        self.requests.append(request)
        self.containing.append(())
        self.parts.append((part, topics))
        return self._result(request, 0)

    def _result(self, request: RetrievalRequest, found: int) -> RetrievalResult:
        evidence = self._evidence if found else ()
        returned = len(evidence)
        return RetrievalResult(
            evidence=evidence,
            has_sufficient_evidence=returned > 0,
            trace=RetrievalTrace(
                request_id=request.request_id or uuid4(),
                embedding_version="embed-test",
                index_version="v1",
                hits_retrieved=returned,
                hits_after_validation=returned,
                hits_after_threshold=returned,
                returned=returned,
                rounds=1,
                top_k=request.top_k,
                min_retrieval_score=0.857,
                embed_ms=0.0,
                search_ms=0.0,
                validate_ms=0.0,
            ),
        )


class _Opener:
    @asynccontextmanager
    async def open(self, context: TenantContext) -> AsyncIterator[object]:
        del context
        yield None


def _technical(request_id: object) -> QuestionDebug:
    from uuid import UUID

    if not isinstance(request_id, UUID):
        request_id = uuid4()
    return QuestionDebug(
        request_id=request_id,
        embedding_version="embed-test",
        index_version="v1",
        hits_retrieved=1,
        hits_after_validation=1,
        hits_after_threshold=1,
        returned=1,
        rounds=1,
        top_k=5,
        min_retrieval_score=0.857,
        embed_ms=1.0,
        search_ms=1.0,
        validate_ms=1.0,
        generation_ms=2.0,
        llm_called=True,
        token_estimate=12,
        evidence=(),
    )


def _principal(
    kind: str = "member",
    *,
    tenant_id: object = None,
    user_id: object = None,
) -> AuthPrincipal:
    from uuid import UUID

    tenant = uuid4() if not isinstance(tenant_id, UUID) else tenant_id
    user = uuid4() if not isinstance(user_id, UUID) else user_id
    if kind == "key":
        return AuthPrincipal(
            type=PrincipalType.API_KEY,
            user_id=None,
            tenant_id=tenant,
            role=None,
            scopes=(ApiKeyScope.QUERY,),
            api_key_id=user,
        )
    return AuthPrincipal(
        type=PrincipalType.MEMBER,
        user_id=user,
        tenant_id=tenant,
        role=TenantRole.MEMBER,
        scopes=(),
    )


def _context(principal: AuthPrincipal) -> TenantContext:
    if principal.tenant_id is None:
        message = "La prueba necesita empresa"
        raise AssertionError(message)
    return TenantContext(tenant_id=principal.tenant_id, principal=principal)


def _service(
    questions: _CountingQuestions | QuestionService | None = None,
) -> tuple[ConversationService, MemoryHistoryStore, _Clock]:
    store = MemoryHistoryStore()
    clock = _Clock(_NOW)
    service = ConversationService(
        store,  # type: ignore[arg-type]
        questions if questions is not None else _CountingQuestions(),  # type: ignore[arg-type]
        clock=clock,
    )
    return service, store, clock


def _ask(
    service: ConversationService,
    context: TenantContext,
    question: str,
    *,
    key: str | None = None,
    conversation_id: object = None,
) -> QuestionAnswer:
    from uuid import UUID

    conversation = conversation_id if isinstance(conversation_id, UUID) else None
    return asyncio.run(
        service.ask(
            context,
            QuestionRequest(question=question, request_id=uuid4(), conversation_id=conversation),
            idempotency_key=key,
        )
    )


def test_a_standalone_question_retrieves_only_itself() -> None:
    service, _store, retrieval, _llm = _search()
    context = _context(_principal())

    _ask(service, context, "primera pregunta independiente")
    _ask(service, context, "segunda pregunta independiente")

    assert [item.question for item in retrieval.requests] == [
        "primera pregunta independiente",
        "segunda pregunta independiente",
    ]


def test_the_first_question_of_a_conversation_retrieves_only_itself() -> None:
    service, _store, retrieval, _llm = _search()
    context = _context(_principal())
    created = asyncio.run(service.create(context, None))

    _ask(service, context, "primera pregunta independiente", conversation_id=created.id)

    assert retrieval.requests[0].question == "primera pregunta independiente"


def test_a_part_follow_up_keeps_the_latest_labeled_topic(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.conversations.service")
    service, store, retrieval, _llm = _search()
    context = _context(_principal())
    first = _ask(service, context, _THEOREM)
    second = _ask(service, context, _FOLLOW_UP, conversation_id=first.conversation_id)
    third = _ask(service, context, _EXAMPLE, conversation_id=first.conversation_id)
    joined = f"{_THEOREM}\n{_FOLLOW_UP}"

    assert [item.question for item in retrieval.requests] == [
        _THEOREM,
        joined,
        f"{_THEOREM}\n{_EXAMPLE}",
    ]
    assert retrieval.containing == [("8.4.4",), (), ()]
    assert retrieval.parts == [
        ("demostracion", (("teorema", "8.4.4"),)),
        ("ejemplo", (("teorema", "8.4.4"),)),
    ]
    assert PUBLIC_ABSTENTION not in retrieval.requests[1].question
    assert PUBLIC_ABSTENTION not in retrieval.requests[2].question
    assert second.answer == PUBLIC_ABSTENTION
    assert third.answer == PUBLIC_ABSTENTION
    assert second.history_scope == "retrieval"
    assert third.history_scope == "retrieval"
    assert _user_texts(service, context, first.conversation_id) == [_THEOREM, _FOLLOW_UP, _EXAMPLE]
    assert joined not in caplog.text
    assert "8.4.4" not in caplog.text
    assert _FOLLOW_UP not in caplog.text
    assert "expanded=true" in caplog.text
    rendered = repr(store.traces)
    assert joined not in rendered
    assert _THEOREM not in rendered
    assert _FOLLOW_UP not in rendered


def test_a_labeled_question_does_not_drag_the_previous_topic() -> None:
    service, _store, retrieval, _llm = _search()
    context = _context(_principal())
    first = _ask(service, context, _THEOREM)

    _ask(service, context, "teorema 7.4.4", conversation_id=first.conversation_id)

    assert retrieval.requests[1].question == "teorema 7.4.4"
    assert retrieval.parts == []


def test_a_plain_follow_up_after_a_part_does_not_recover_the_first_topic() -> None:
    service, _store, retrieval, _llm = _search()
    context = _context(_principal())
    first = _ask(service, context, _THEOREM)
    _ask(service, context, _FOLLOW_UP, conversation_id=first.conversation_id)

    _ask(service, context, "¿qué hipótesis usa?", conversation_id=first.conversation_id)

    assert retrieval.requests[2].question == f"{_FOLLOW_UP}\n¿qué hipótesis usa?"
    assert "8.4.4" not in retrieval.requests[2].question


def test_the_model_sees_only_the_question_just_written() -> None:
    service, _store, retrieval, llm = _search(
        evidence=(_evidence(),),
        script=[_json(_PREVIOUS_ANSWER), _json(_NEXT_ANSWER)],
    )
    context = _context(_principal())
    opening = "cuantos dias de vacaciones hay"
    follow = "y los festivos"
    first = _ask(service, context, opening)
    second = _ask(service, context, follow, conversation_id=first.conversation_id)
    prompt = llm.requests[1].user_prompt

    assert opening in retrieval.requests[1].question
    assert follow in retrieval.requests[1].question
    assert _PREVIOUS_ANSWER not in retrieval.requests[1].question
    assert f"Pregunta:\n{follow}\n" in prompt
    assert opening not in prompt
    assert _PREVIOUS_ANSWER not in prompt
    assert second.answer == _NEXT_ANSWER
    assert _user_texts(service, context, first.conversation_id)[1] == follow


def test_idempotency_uses_the_original_question() -> None:
    service, _store, retrieval, _llm = _search()
    context = _context(_principal())
    first = _ask(service, context, _THEOREM)
    conversation_id = first.conversation_id
    second = _ask(service, context, _FOLLOW_UP, key="misma-clave", conversation_id=conversation_id)
    replay = _ask(service, context, _FOLLOW_UP, key="misma-clave", conversation_id=conversation_id)
    written = QuestionRequest(question=_FOLLOW_UP, conversation_id=conversation_id)
    expanded = QuestionRequest(
        question=f"{_THEOREM}\n{_FOLLOW_UP}",
        conversation_id=conversation_id,
    )

    assert len(retrieval.requests) == 2
    assert replay.message_id == second.message_id
    assert replay.answer == second.answer
    assert canonical_payload_hash(written) != canonical_payload_hash(expanded)


def test_a_foreign_conversation_does_not_contribute_text() -> None:
    service, _store, retrieval, _llm = _search()
    tenant = uuid4()
    context = _context(_principal(tenant_id=tenant, user_id=uuid4()))
    first = _ask(service, context, _THEOREM)
    stranger = _context(_principal(tenant_id=tenant, user_id=uuid4()))
    other_company = _context(_principal())

    with pytest.raises(NotFoundError):
        _ask(service, stranger, _FOLLOW_UP, conversation_id=first.conversation_id)
    with pytest.raises(NotFoundError):
        _ask(service, other_company, _FOLLOW_UP, conversation_id=first.conversation_id)

    assert [item.question for item in retrieval.requests] == [_THEOREM]


def _search(
    *,
    evidence: tuple[Evidence, ...] = (),
    script: list[str] | None = None,
) -> tuple[ConversationService, MemoryHistoryStore, _Retrieval, FakeLLMProvider]:
    retrieval = _Retrieval(evidence)
    llm = FakeLLMProvider(script or [])
    questions = QuestionService(
        retrieval,  # type: ignore[arg-type]
        llm,
        _Opener(),
        max_context_chars=1000,
        min_retrieval_score=0.0,
        min_lexical_score=0.0,
    )
    service, store, _clock = _service(questions)
    return service, store, retrieval, llm


def _evidence() -> Evidence:
    return Evidence(
        chunk_id="chunk-1",
        document_id=uuid4(),
        document_version_id=uuid4(),
        source="politica.md",
        page_start=1,
        page_end=1,
        section=None,
        excerpt="fragmento de apoyo",
        score=0.99,
        position=1,
    )


def _json(answer: str) -> str:
    return json.dumps(
        {"answer": answer, "abstained": False, "cited_chunk_ids": ["c1"]},
        ensure_ascii=False,
    )


def _user_texts(
    service: ConversationService,
    context: TenantContext,
    conversation_id: object,
) -> list[str]:
    from uuid import UUID

    if not isinstance(conversation_id, UUID):
        message = "La prueba necesita una conversación"
        raise AssertionError(message)
    detail = asyncio.run(service.get(context, conversation_id))
    return [item.content for item in detail.messages if item.role is MessageRole.USER]


def test_the_same_key_does_not_call_the_model_again() -> None:
    questions = _CountingQuestions()
    service, store, _clock = _service(questions)
    context = _context(_principal())
    first = _ask(service, context, _PLANTED_QUESTION, key="clave-estable")
    second = _ask(service, context, _PLANTED_QUESTION, key="clave-estable")

    assert questions.seen == [_PLANTED_QUESTION]
    assert second.message_id == first.message_id
    assert second.conversation_id == first.conversation_id
    assert sum(len(item.messages) for item in store.conversations.values()) == 2
    assert len(store.traces) == 1
    assert len(store.usage) == 1
    with pytest.raises(ConflictError, match="otra pregunta"):
        _ask(service, context, "otra pregunta distinta", key="clave-estable")
    assert questions.seen == [_PLANTED_QUESTION]
    assert len(store.traces) == 1
    assert len(store.usage) == 1


def test_a_pending_key_is_a_conflict() -> None:
    questions = _CountingQuestions()
    service, store, clock = _service(questions)
    context = _context(_principal())
    request = QuestionRequest(question=_PLANTED_QUESTION, request_id=uuid4())
    asyncio.run(
        store.claim(
            context,
            owner=owner_from(context.principal),
            key_hash=hash_key("clave-pendiente"),
            payload_hash=canonical_payload_hash(request),
            request_id=request.request_id or uuid4(),
            now=clock.current,
        )
    )

    with pytest.raises(ConflictError, match="en curso"):
        asyncio.run(service.ask(context, request, idempotency_key="clave-pendiente"))
    assert questions.seen == []


def test_feedback_is_only_for_the_author_and_an_assistant_message() -> None:
    service, store, _clock = _service()
    tenant = uuid4()
    author = _principal(tenant_id=tenant, user_id=uuid4())
    other = _principal(tenant_id=tenant, user_id=uuid4())
    asked = _ask(service, _context(author), _PLANTED_QUESTION)
    detail = asyncio.run(service.get(_context(author), asked.conversation_id or uuid4()))
    user_message = next(item for item in detail.messages if item.role.value == "user")
    assistant = next(item for item in detail.messages if item.role.value == "assistant")

    with pytest.raises(NotFoundError):
        asyncio.run(
            service.submit_feedback(
                _context(author),
                user_message.id,
                FeedbackRating.HELPFUL,
                None,
            )
        )
    with pytest.raises(NotFoundError):
        asyncio.run(
            service.submit_feedback(
                _context(other),
                assistant.id,
                FeedbackRating.HELPFUL,
                None,
            )
        )
    first = asyncio.run(
        service.submit_feedback(
            _context(author),
            assistant.id,
            FeedbackRating.HELPFUL,
            "  ",
        )
    )
    second = asyncio.run(
        service.submit_feedback(
            _context(author),
            assistant.id,
            FeedbackRating.NOT_HELPFUL,
            "no citó la página",
        )
    )

    assert first.reason is None
    assert second.rating is FeedbackRating.NOT_HELPFUL
    assert len(store.feedback) == 1
    with pytest.raises(NotFoundError):
        asyncio.run(service.get(_context(other), asked.conversation_id or uuid4()))


def test_usage_json_omits_the_planted_question_and_answer() -> None:
    service, store, _clock = _service()
    context = _context(_principal())
    _ask(service, context, _PLANTED_QUESTION)
    rendered = daily_response(
        asyncio.run(service.daily_usage(context, start=None, end=None))
    ).model_dump_json()

    assert _PLANTED_QUESTION not in rendered
    assert _PLANTED_ANSWER not in rendered
    assert json.loads(rendered)["items"][0]["questions"] == 1
    assert store.traces
    assert _PLANTED_QUESTION not in repr(store.traces)


def test_an_expired_conversation_is_hidden() -> None:
    service, _store, clock = _service()
    context = _context(_principal())
    asked = _ask(service, context, "hola")
    clock.current = _NOW + timedelta(days=31)

    with pytest.raises(NotFoundError):
        asyncio.run(service.get(context, asked.conversation_id or uuid4()))


def test_two_api_keys_do_not_share_a_conversation() -> None:
    service, _store, _clock = _service()
    tenant = uuid4()
    first = _principal("key", tenant_id=tenant, user_id=uuid4())
    second = _principal("key", tenant_id=tenant, user_id=uuid4())
    asked = _ask(service, _context(first), "hola desde la clave")

    with pytest.raises(NotFoundError):
        asyncio.run(service.get(_context(second), asked.conversation_id or uuid4()))


def test_canonical_payload_ignores_spacing_and_document_order() -> None:
    first_document = uuid4()
    second_document = uuid4()
    conversation_id = uuid4()
    left = QuestionRequest(
        question="hola   mundo",
        top_k=5,
        document_ids=(second_document, first_document),
        conversation_id=conversation_id,
    )
    right = QuestionRequest(
        question="hola mundo",
        top_k=5,
        document_ids=(first_document, second_document),
        conversation_id=conversation_id,
    )
    changed = QuestionRequest(
        question="hola mundo",
        top_k=5,
        document_ids=(first_document, second_document),
        conversation_id=None,
    )

    assert canonical_payload_hash(left) == canonical_payload_hash(right)
    assert canonical_payload_hash(left) != canonical_payload_hash(changed)


def test_canonical_payload_includes_llm_only_when_it_is_present() -> None:
    plain = QuestionRequest(question="hola")
    blank = QuestionRequest(question="hola", llm="  ")
    local = QuestionRequest(question="hola", llm=" ollama:qwen3.5:2b ")
    same = QuestionRequest(question="hola", llm="ollama:qwen3.5:2b")
    external = QuestionRequest(question="hola", llm="external")

    assert canonical_payload_hash(plain) == canonical_payload_hash(blank)
    assert canonical_payload_hash(local) == canonical_payload_hash(same)
    assert canonical_payload_hash(plain) != canonical_payload_hash(local)
    assert canonical_payload_hash(local) != canonical_payload_hash(external)
