"""Historial, feedback y uso contra PostgreSQL desechable."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.deps import require_authenticated
from app.api.questions import get_question_service
from app.config import Environment, LogLevel, Settings
from app.conversations.payload import canonical_payload_hash, hash_key
from app.conversations.sql import SqlAlchemyHistoryStore
from app.domain.entities import AuthPrincipal, TenantContext
from app.domain.enums import PrincipalType, QuestionOutcome, TenantRole, UsageKind
from app.domain.errors import AuthenticationError
from app.main import create_app
from app.persistence.models.history import QuestionIdempotencyModel
from app.persistence.models.tenant import TenantModel
from app.persistence.unit_of_work import UnitOfWork
from app.persistence.usage import record_usage_event
from app.questions.models import PublicCitation, QuestionAnswer, QuestionDebug, QuestionRequest
from tests.integration.db_helpers import (
    database_url_for,
    drop_and_create_database,
    drop_database,
    run_alembic,
    session_factory_for,
)

pytestmark = pytest.mark.integration

HISTORY_DB = "engine_rag_history_test"
_PLANTED_QUESTION = "PREGUNTA-PLANTA-HISTORIAL"
_PLANTED_ANSWER = "RESPUESTA-PLANTA-HISTORIAL"
_PLANTED_EXCERPT = "CITA-PLANTA-HISTORIAL"
_TEXT_COLUMNS = frozenset({"content", "question", "answer", "prompt", "excerpt", "vector"})


class _Identity:
    def __init__(self) -> None:
        self.principal: AuthPrincipal | None = None


class _Scripted:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False
        self.queries: list[str] = []

    async def answer(
        self,
        context: TenantContext,
        request: QuestionRequest,
        *,
        retrieval_query: str | None = None,
        part: str | None = None,
        topics: tuple[tuple[str, str], ...] = (),
    ) -> QuestionAnswer:
        del context, part, topics
        self.calls += 1
        self.queries.append(request.question if retrieval_query is None else retrieval_query)
        if self.fail:
            from app.generation.errors import LLMTimeoutError

            raise LLMTimeoutError("tiempo")
        request_id = request.request_id if request.request_id is not None else uuid4()
        abstained = request.question.startswith("abstener")
        return QuestionAnswer(
            request_id=request_id,
            answer="No está en los documentos disponibles" if abstained else _PLANTED_ANSWER,
            abstained=abstained,
            citations=()
            if abstained
            else (
                PublicCitation(
                    document_id=uuid4(),
                    document_name="politica.md",
                    page=1,
                    section=None,
                    excerpt=_PLANTED_EXCERPT,
                ),
            ),
            pipeline_version="embed|v1|prompt-v4|fake",
            technical=QuestionDebug(
                request_id=request_id,
                embedding_version="embed-test",
                index_version="v1",
                hits_retrieved=1,
                hits_after_validation=1,
                hits_after_threshold=1,
                returned=0 if abstained else 1,
                rounds=1,
                top_k=request.top_k,
                min_retrieval_score=0.857,
                embed_ms=1.0,
                search_ms=1.0,
                validate_ms=1.0,
                generation_ms=0.0 if abstained else 3.0,
                llm_called=not abstained,
                token_estimate=0 if abstained else 8,
                evidence=(),
            ),
        )


@pytest.fixture(scope="module")
def history_database() -> Iterator[None]:
    """Base desechable. No usa `engine_rag`."""
    asyncio.run(drop_and_create_database(HISTORY_DB))
    run_alembic("upgrade", "head", database_url=database_url_for(HISTORY_DB))
    try:
        yield
    finally:
        asyncio.run(drop_database(HISTORY_DB))


def test_traces_and_usage_have_no_text_columns(history_database: None) -> None:
    columns = asyncio.run(_columns())

    assert _TEXT_COLUMNS.isdisjoint(columns["query_traces"])
    assert _TEXT_COLUMNS.isdisjoint(columns["usage_events"])
    assert "content" in columns["messages"]


def test_daily_percentiles_come_from_postgres(history_database: None) -> None:
    tenant_id = asyncio.run(_insert_tenant("percentiles-historia"))
    when = datetime(2026, 1, 15, 10, tzinfo=UTC)
    context = _context(_member(tenant_id, uuid4()))
    asyncio.run(_insert_latencies(context, when, (10.0, 20.0, 30.0, 40.0, 100.0)))

    async def _read() -> tuple[float | None, float | None]:
        async with session_factory_for(HISTORY_DB) as factory:
            store = SqlAlchemyHistoryStore(factory)
            days = await store.daily_usage(
                context,
                start=datetime(2026, 1, 15, tzinfo=UTC),
                end=datetime(2026, 1, 16, tzinfo=UTC),
            )
        assert len(days) == 1
        return days[0].totals.p50_ms, days[0].totals.p95_ms

    p50, p95 = asyncio.run(_read())
    assert p50 == pytest.approx(30)
    assert p95 == pytest.approx(88)


def test_ownership_idempotency_feedback_and_aggregates(history_database: None) -> None:
    tenant_a = asyncio.run(_insert_tenant("empresa-a-historia"))
    tenant_b = asyncio.run(_insert_tenant("empresa-b-historia"))
    owner_a = uuid4()
    other_a = uuid4()
    admin_a = uuid4()
    owner_b = uuid4()
    scripted = _Scripted()
    identity = _Identity()
    with _client(scripted, identity) as client:
        identity.principal = _member(tenant_a, owner_a)
        created = client.post("/v1/questions", json={"question": _PLANTED_QUESTION})
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["history_scope"] == "retrieval"
        assert body["answer"] == _PLANTED_ANSWER
        conversation_id = body["conversation_id"]
        message_id = body["message_id"]
        assert scripted.calls == 1

        identity.principal = _member(tenant_a, other_a)
        assert client.get(f"/v1/conversations/{conversation_id}").status_code == 404
        identity.principal = _admin(tenant_a, admin_a)
        assert client.get(f"/v1/conversations/{conversation_id}").status_code == 404
        identity.principal = _member(tenant_b, owner_b)
        assert client.get(f"/v1/conversations/{conversation_id}").status_code == 404

        identity.principal = _member(tenant_a, owner_a)
        own = client.get(f"/v1/conversations/{conversation_id}")
        assert own.status_code == 200, own.text
        assert _PLANTED_QUESTION in own.text
        replay = client.post(
            "/v1/questions",
            json={"question": f"  {_PLANTED_QUESTION}  "},
            headers={"Idempotency-Key": "clave-historial"},
        )
        assert replay.status_code == 200, replay.text
        # La primera llamada no llevaba clave, así que esta sí llama al modelo.
        assert scripted.calls == 2
        repeated = client.post(
            "/v1/questions",
            json={"question": _PLANTED_QUESTION},
            headers={"Idempotency-Key": "clave-historial"},
        )
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["message_id"] == replay.json()["message_id"]
        assert scripted.calls == 2
        conflict = client.post(
            "/v1/questions",
            json={"question": "otra pregunta con la misma clave"},
            headers={"Idempotency-Key": "clave-historial"},
        )
        assert conflict.status_code == 409
        assert scripted.calls == 2

        asyncio.run(
            _pending(
                tenant_a,
                owner_a,
                "clave-en-curso",
                QuestionRequest(question="pregunta en curso", top_k=5),
            )
        )
        pending = client.post(
            "/v1/questions",
            json={"question": "pregunta en curso"},
            headers={"Idempotency-Key": "clave-en-curso"},
        )
        assert pending.status_code == 409
        assert scripted.calls == 2

        abstained = client.post("/v1/questions", json={"question": "abstener sobre mongolia"})
        assert abstained.status_code == 200, abstained.text
        assert abstained.json()["abstained"] is True
        scripted.fail = True
        failed = client.post(
            "/v1/questions",
            json={"question": "falla el modelo"},
            headers={"Idempotency-Key": "clave-falla"},
        )
        assert failed.status_code == 503
        scripted.fail = False
        retried = client.post(
            "/v1/questions",
            json={"question": "falla el modelo"},
            headers={"Idempotency-Key": "clave-falla"},
        )
        assert retried.status_code == 200, retried.text

        identity.principal = _member(tenant_b, owner_b)
        other_company = client.post(
            "/v1/questions",
            json={"question": "pregunta de la otra empresa"},
        )
        assert other_company.status_code == 200, other_company.text

        identity.principal = _member(tenant_a, owner_a)
        detail = client.get(f"/v1/conversations/{conversation_id}").json()
        user_message = next(item for item in detail["messages"] if item["role"] == "user")
        rated = client.put(
            f"/v1/messages/{message_id}/feedback",
            json={"rating": "helpful", "reason": "  "},
        )
        assert rated.status_code == 200, rated.text
        assert rated.json()["reason"] is None
        updated = client.put(
            f"/v1/messages/{message_id}/feedback",
            json={"rating": "not_helpful", "reason": "faltó la página"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["rating"] == "not_helpful"
        reloaded = client.get(f"/v1/conversations/{conversation_id}")
        assert reloaded.status_code == 200, reloaded.text
        reloaded_messages = reloaded.json()["messages"]
        assistant_message = next(item for item in reloaded_messages if item["role"] == "assistant")
        assert assistant_message["feedback_rating"] == "not_helpful"
        assert "reason" not in assistant_message
        assert user_message["feedback_rating"] is None
        reloaded_user = next(item for item in reloaded_messages if item["role"] == "user")
        assert reloaded_user["feedback_rating"] is None
        assert (
            client.put(
                f"/v1/messages/{user_message['id']}/feedback",
                json={"rating": "helpful"},
            ).status_code
            == 404
        )
        identity.principal = _member(tenant_a, other_a)
        assert (
            client.put(
                f"/v1/messages/{message_id}/feedback",
                json={"rating": "helpful"},
            ).status_code
            == 404
        )

        identity.principal = _member(tenant_a, owner_a)
        assert client.delete(f"/v1/conversations/{conversation_id}").status_code == 204
        assert client.get(f"/v1/conversations/{conversation_id}").status_code == 404

        identity.principal = _admin(tenant_a, admin_a)
        daily = client.get("/v1/usage/daily")
        assert daily.status_code == 200, daily.text
        rendered = daily.text
        assert _PLANTED_QUESTION not in rendered
        assert _PLANTED_ANSWER not in rendered
        assert _PLANTED_EXCERPT not in rendered
        assert "tenant_id" not in daily.json()["items"][0]
        totals = _sum_items(daily.json()["items"])
        assert totals["questions"] >= 4
        assert totals["abstentions"] >= 1
        assert totals["errors"] >= 1
        identity.principal = _member(tenant_a, owner_a)
        assert client.get("/v1/usage/daily").status_code == 403
        identity.principal = _admin(tenant_b, uuid4())
        foreign_daily = client.get("/v1/usage/daily")
        assert foreign_daily.status_code == 200, foreign_daily.text
        assert _PLANTED_QUESTION not in foreign_daily.text
        assert _sum_items(foreign_daily.json()["items"])["questions"] == 1

        identity.principal = _provider()
        assert client.get("/v1/conversations").status_code == 403
        assert client.get(f"/v1/conversations/{conversation_id}").status_code == 403
        provider = client.get("/v1/provider/usage")
        assert provider.status_code == 200, provider.text
        payload = provider.json()
        assert "tenant_id" not in payload
        assert _PLANTED_QUESTION not in provider.text
        assert _PLANTED_ANSWER not in provider.text
        assert payload["questions"] >= totals["questions"] + 1

    assert _PLANTED_QUESTION not in asyncio.run(_trace_blob())
    assert _PLANTED_ANSWER not in asyncio.run(_trace_blob())
    assert _PLANTED_EXCERPT not in asyncio.run(_trace_blob())


def test_a_part_follow_up_keeps_the_latest_labeled_topic(history_database: None) -> None:
    tenant = asyncio.run(_insert_tenant("empresa-seguimiento"))
    owner = uuid4()
    other = uuid4()
    scripted = _Scripted()
    identity = _Identity()
    theorem = "que dice el teorema 8.4.4"
    follow = "dime la demostracion"
    example = "y un ejemplo"
    with _client(scripted, identity) as client:
        identity.principal = _member(tenant, owner)
        created = client.post("/v1/questions", json={"question": theorem})
        assert created.status_code == 200, created.text
        conversation_id = created.json()["conversation_id"]
        assert created.json()["history_scope"] == "retrieval"
        second = client.post(
            "/v1/questions",
            json={"question": follow, "conversation_id": conversation_id},
        )
        assert second.status_code == 200, second.text
        third = client.post(
            "/v1/questions",
            json={"question": example, "conversation_id": conversation_id},
        )
        assert third.status_code == 200, third.text
        identity.principal = _member(tenant, other)
        missing = client.post(
            "/v1/questions",
            json={"question": follow, "conversation_id": conversation_id},
        )
        assert missing.status_code == 404
        identity.principal = _member(tenant, owner)
        detail = client.get(f"/v1/conversations/{conversation_id}")
        assert detail.status_code == 200, detail.text

    assert scripted.queries == [theorem, f"{theorem}\n{follow}", f"{theorem}\n{example}"]
    assert _PLANTED_ANSWER not in scripted.queries[1]
    users = [item["content"] for item in detail.json()["messages"] if item["role"] == "user"]
    assert users == [theorem, follow, example]


def _client(scripted: _Scripted, identity: _Identity) -> TestClient:
    application = create_app(
        Settings(
            environment=Environment.TEST,
            log_level=LogLevel.INFO,
            database_url=database_url_for(HISTORY_DB),
            jwt_secret="integration-history-secret-32b!!",
        ),
        postgres_check=_ready,
        qdrant_check=_ready,
        ollama_check=_ready,
    )

    def _questions() -> _Scripted:
        return scripted

    async def _authenticated() -> AuthPrincipal:
        if identity.principal is None:
            raise AuthenticationError
        return identity.principal

    application.dependency_overrides[get_question_service] = _questions
    application.dependency_overrides[require_authenticated] = _authenticated
    return TestClient(application)


def _member(tenant_id: UUID, user_id: UUID) -> AuthPrincipal:
    return AuthPrincipal(
        type=PrincipalType.MEMBER,
        user_id=user_id,
        tenant_id=tenant_id,
        role=TenantRole.MEMBER,
        scopes=(),
    )


def _admin(tenant_id: UUID, user_id: UUID) -> AuthPrincipal:
    return AuthPrincipal(
        type=PrincipalType.TENANT_ADMIN,
        user_id=user_id,
        tenant_id=tenant_id,
        role=TenantRole.TENANT_ADMIN,
        scopes=(),
    )


def _provider() -> AuthPrincipal:
    return AuthPrincipal(
        type=PrincipalType.PROVIDER_ADMIN,
        user_id=uuid4(),
        tenant_id=None,
        role=None,
        scopes=(),
    )


def _context(principal: AuthPrincipal) -> TenantContext:
    if principal.tenant_id is None:
        message = "La prueba de percentiles necesita empresa"
        raise AssertionError(message)
    return TenantContext(tenant_id=principal.tenant_id, principal=principal)


def _sum_items(items: list[dict[str, object]]) -> dict[str, int]:
    questions = 0
    abstentions = 0
    errors = 0
    for item in items:
        questions += int(item["questions"])  # type: ignore[arg-type]
        abstentions += int(item["abstentions"])  # type: ignore[arg-type]
        errors += int(item["errors"])  # type: ignore[arg-type]
    return {"questions": questions, "abstentions": abstentions, "errors": errors}


async def _ready() -> bool:
    return True


async def _insert_tenant(slug: str) -> UUID:
    tenant_id = uuid4()
    now = datetime.now(UTC)
    async with session_factory_for(HISTORY_DB) as factory, factory() as session:
        session.add(
            TenantModel(
                id=tenant_id,
                name=slug,
                slug=slug,
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    return tenant_id


async def _insert_latencies(
    context: TenantContext,
    when: datetime,
    latencies: tuple[float, ...],
) -> None:
    async with session_factory_for(HISTORY_DB) as factory, UnitOfWork(factory) as unit:
        for latency in latencies:
            await record_usage_event(
                unit,
                context,
                kind=UsageKind.QUESTION,
                source_id=uuid4(),
                occurred_at=when,
                quantity=1,
                outcome=QuestionOutcome.ANSWERED,
                latency_ms=latency,
                token_estimate=1,
            )
        await unit.commit()


async def _pending(tenant_id: UUID, owner_id: UUID, key: str, request: QuestionRequest) -> None:
    now = datetime.now(UTC)
    async with session_factory_for(HISTORY_DB) as factory, factory() as session:
        session.add(
            QuestionIdempotencyModel(
                id=uuid4(),
                tenant_id=tenant_id,
                owner_kind="user",
                owner_id=owner_id,
                key_hash=hash_key(key),
                payload_hash=canonical_payload_hash(request),
                request_id=uuid4(),
                status="pending",
                created_at=now,
                completed_at=None,
            )
        )
        await session.commit()


async def _columns() -> dict[str, set[str]]:
    async with session_factory_for(HISTORY_DB) as factory, factory() as session:
        result = await session.execute(
            text(
                """
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name IN ('messages', 'query_traces', 'usage_events')
                    """
            )
        )
    grouped: dict[str, set[str]] = {"messages": set(), "query_traces": set(), "usage_events": set()}
    for table_name, column_name in result.all():
        grouped[str(table_name)].add(str(column_name))
    return grouped


async def _trace_blob() -> str:
    async with session_factory_for(HISTORY_DB) as factory, factory() as session:
        result = await session.execute(
            text(
                """
                    SELECT evidence::text, embedding_version, index_version,
                           pipeline_version, result_code
                    FROM query_traces
                    """
            )
        )
    return "\n".join(" ".join(str(value) for value in row) for row in result.all())
