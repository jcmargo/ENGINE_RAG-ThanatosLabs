"""Contrato HTTP de preguntas: autenticación, aislamiento, errores y SSE."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import require_authenticated
from app.api.history import get_conversation_service
from app.config import Environment, LogLevel, Settings, repository_root
from app.domain.entities import AuthPrincipal, TenantContext
from app.domain.enums import ApiKeyScope, PrincipalType, TenantRole
from app.domain.errors import InvalidStateError, NotFoundError
from app.domain.history import HISTORY_SCOPE
from app.generation.directory import ModelOption
from app.generation.errors import LLMTimeoutError
from app.generation.fake import FakeLLMProvider
from app.generation.models import PUBLIC_ABSTENTION
from app.generation.prompt import SYSTEM_PROMPT
from app.main import create_app
from app.questions.models import QuestionAnswer, QuestionRequest
from app.questions.service import QuestionService
from app.retrieval.contracts import Evidence, RetrievalRequest, RetrievalResult, RetrievalTrace

_EXCERPT = "ThanatosLabs otorga 18 días hábiles de vacaciones."
_FORBIDDEN_MEMBER_KEYS = {
    "score",
    "prompt",
    "vector",
    "reasoning",
    "trace",
    "chunk_id",
    "storage_key",
    "path",
    "debug",
}


class _Retrieval:
    def __init__(
        self,
        result: RetrievalResult | None = None,
        error: Exception | None = None,
        *,
        reject_filters: bool = False,
    ) -> None:
        self._result = result
        self._error = error
        self._reject_filters = reject_filters
        self.contexts: list[TenantContext] = []
        self.requests: list[RetrievalRequest] = []

    async def retrieve(
        self,
        context: TenantContext,
        documents: object,
        request: RetrievalRequest,
        *,
        candidate_floor: float | None = None,
        containing: tuple[str, ...] = (),
    ) -> RetrievalResult:
        del documents, candidate_floor, containing
        self.contexts.append(context)
        self.requests.append(request)
        if self._reject_filters and request.document_ids:
            raise NotFoundError("Documento no encontrado")
        if self._error is not None:
            raise self._error
        if self._result is None:
            message = "La recuperación falsa no tiene resultado"
            raise AssertionError(message)
        return self._result


class _Opener:
    @asynccontextmanager
    async def open(self, context: TenantContext) -> AsyncIterator[object]:
        del context
        yield None


def _settings() -> Settings:
    return Settings(
        environment=Environment.TEST,
        log_level=LogLevel.INFO,
        api_host="127.0.0.1",
        api_port=8000,
    )


async def _ready() -> bool:
    return True


def _principal(kind: str, tenant_id: UUID | None = None) -> AuthPrincipal:
    tenant = uuid4() if tenant_id is None else tenant_id
    if kind == "admin":
        return AuthPrincipal(
            type=PrincipalType.TENANT_ADMIN,
            user_id=uuid4(),
            tenant_id=tenant,
            role=TenantRole.TENANT_ADMIN,
            scopes=(),
        )
    if kind == "key":
        return AuthPrincipal(
            type=PrincipalType.API_KEY,
            user_id=None,
            tenant_id=tenant,
            role=None,
            scopes=(ApiKeyScope.QUERY,),
        )
    if kind == "key-read":
        return AuthPrincipal(
            type=PrincipalType.API_KEY,
            user_id=None,
            tenant_id=tenant,
            role=None,
            scopes=(ApiKeyScope.DOCUMENTS_READ,),
        )
    if kind == "provider":
        return AuthPrincipal(
            type=PrincipalType.PROVIDER_ADMIN,
            user_id=uuid4(),
            tenant_id=None,
            role=None,
            scopes=(),
        )
    return AuthPrincipal(
        type=PrincipalType.MEMBER,
        user_id=uuid4(),
        tenant_id=tenant,
        role=TenantRole.MEMBER,
        scopes=(),
    )


def _evidence(source: str = "politica-vacaciones.md", excerpt: str = _EXCERPT) -> Evidence:
    return Evidence(
        chunk_id="chunk-vacaciones",
        document_id=uuid4(),
        document_version_id=uuid4(),
        source=source,
        page_start=1,
        page_end=1,
        section=None,
        excerpt=excerpt,
        score=0.91,
        position=1,
    )


def _result(*evidence: Evidence) -> RetrievalResult:
    returned = len(evidence)
    return RetrievalResult(
        evidence=evidence,
        has_sufficient_evidence=bool(evidence),
        trace=RetrievalTrace(
            request_id=uuid4(),
            embedding_version="embed-test",
            index_version="v1",
            hits_retrieved=returned,
            hits_after_validation=returned,
            hits_after_threshold=returned,
            returned=returned,
            rounds=1,
            top_k=5,
            min_retrieval_score=0.857,
            embed_ms=1.0,
            search_ms=1.0,
            validate_ms=1.0,
        ),
    )


def _json(answer: str, cited: list[str]) -> str:
    return json.dumps(
        {"answer": answer, "abstained": False, "cited_chunk_ids": cited},
        ensure_ascii=False,
    )


def _service(retrieval: _Retrieval, script: list[str | LLMTimeoutError]) -> QuestionService:
    return QuestionService(
        retrieval,
        FakeLLMProvider(script),
        _Opener(),
        max_context_chars=12_000,
    )


class _DirectHistory:
    """Registrador falso: no abre PostgreSQL."""

    def __init__(self, questions: QuestionService) -> None:
        self._questions = questions

    async def ask(
        self,
        context: TenantContext,
        request: QuestionRequest,
        *,
        idempotency_key: str | None,
    ) -> QuestionAnswer:
        del idempotency_key
        result = await self._questions.answer(context, request)
        return replace(
            result,
            conversation_id=uuid4(),
            message_id=uuid4(),
            history_scope=HISTORY_SCOPE,
        )


def _application(principal: AuthPrincipal | None, service: QuestionService) -> FastAPI:
    application = create_app(
        _settings(),
        postgres_check=_ready,
        qdrant_check=_ready,
        ollama_check=_ready,
    )
    history = _DirectHistory(service)

    def _history() -> _DirectHistory:
        return history

    application.dependency_overrides[get_conversation_service] = _history
    if principal is not None:

        async def _authenticated() -> AuthPrincipal:
            return principal

        application.dependency_overrides[require_authenticated] = _authenticated
    return application


def _ask(
    principal: AuthPrincipal | None,
    service: QuestionService,
    payload: dict[str, object],
    *,
    headers: dict[str, str] | None = None,
    debug: bool = False,
    path: str = "/v1/questions",
) -> object:
    params = {"debug": "true"} if debug else None
    with TestClient(_application(principal, service)) as client:
        return client.post(path, json=payload, headers=headers, params=params)


def _keys(node: object) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str):
                found.add(key)
            found.update(_keys(value))
    elif isinstance(node, list):
        for item in node:
            found.update(_keys(item))
    return found


def _events(body: str) -> list[tuple[str, dict[str, object]]]:
    parsed: list[tuple[str, dict[str, object]]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        name = "message"
        data = ""
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data = line.removeprefix("data:").strip()
        loaded = json.loads(data)
        if not isinstance(loaded, dict):
            message = "El evento SSE no es un objeto"
            raise AssertionError(message)
        parsed.append((name, loaded))
    return parsed


def test_missing_credentials_are_rejected() -> None:
    retrieval = _Retrieval(_result())
    response = _ask(None, _service(retrieval, []), {"question": "hola"})

    assert response.status_code == 401
    assert retrieval.requests == []


def test_an_api_key_without_query_scope_is_forbidden() -> None:
    retrieval = _Retrieval(_result(_evidence()))
    response = _ask(
        _principal("key-read"),
        _service(retrieval, [_json("18 días hábiles", ["c1"])]),
        {"question": "¿Cuántos días?"},
    )

    assert response.status_code == 403
    assert retrieval.requests == []


def test_a_provider_admin_cannot_ask() -> None:
    retrieval = _Retrieval(_result(_evidence()))
    response = _ask(
        _principal("provider"),
        _service(retrieval, [_json("18 días hábiles", ["c1"])]),
        {"question": "¿Cuántos días?"},
    )

    assert response.status_code == 403
    assert retrieval.requests == []


def test_a_member_answer_hides_scores_prompts_and_paths() -> None:
    retrieval = _Retrieval(_result(_evidence()))
    response = _ask(
        _principal("member"),
        _service(retrieval, [_json("18 días hábiles", ["c1"])]),
        {"question": "¿Cuántos días?", "debug": True, "tenant_id": str(uuid4())},
        debug=True,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "18 días hábiles"
    assert body["history_scope"] == "retrieval"
    assert body["abstained"] is False
    assert body["citations"][0]["document_name"] == "politica-vacaciones.md"
    assert body["citations"][0]["section"] is None
    assert _keys(body).isdisjoint(_FORBIDDEN_MEMBER_KEYS)
    assert SYSTEM_PROMPT not in response.text
    assert "\\" not in response.text
    assert "vector" not in response.text


def test_debug_is_present_only_for_the_tenant_admin() -> None:
    admin = _ask(
        _principal("admin"),
        _service(_Retrieval(_result(_evidence())), [_json("18 días hábiles", ["c1"])]),
        {"question": "¿Cuántos días?"},
        debug=True,
    )
    member = _ask(
        _principal("member"),
        _service(_Retrieval(_result(_evidence())), [_json("18 días hábiles", ["c1"])]),
        {"question": "¿Cuántos días?"},
        debug=True,
    )
    key = _ask(
        _principal("key"),
        _service(_Retrieval(_result(_evidence())), [_json("18 días hábiles", ["c1"])]),
        {"question": "¿Cuántos días?"},
        debug=True,
    )

    assert admin.status_code == 200
    debug = admin.json()["debug"]
    assert debug["evidence"][0]["score"] == 0.91
    assert "prompt" not in _keys(debug)
    assert "vector" not in _keys(debug)
    assert SYSTEM_PROMPT not in admin.text
    assert "debug" not in member.json()
    assert "debug" not in key.json()
    assert _keys(member.json()).isdisjoint(_FORBIDDEN_MEMBER_KEYS)


def test_body_header_and_filter_cannot_select_another_tenant() -> None:
    tenant = uuid4()
    other = uuid4()
    foreign_document = uuid4()
    retrieval = _Retrieval(_result(_evidence()), reject_filters=True)
    response = _ask(
        _principal("member", tenant),
        _service(retrieval, [_json("18 días hábiles", ["c1"])]),
        {
            "question": "¿Cuántos días?",
            "tenant_id": str(other),
            "document_ids": [str(foreign_document)],
        },
        headers={"X-Tenant-Id": str(other)},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Documento no encontrado"
    assert str(other) not in response.text
    assert retrieval.contexts[0].tenant_id == tenant
    assert retrieval.requests[0].document_ids == (foreign_document,)

    answered = _Retrieval(_result(_evidence()))
    ok = _ask(
        _principal("member", tenant),
        _service(answered, [_json("18 días hábiles", ["c1"])]),
        {"question": "¿Cuántos días?", "tenant_id": str(other)},
        headers={"X-Tenant-Id": str(other)},
    )

    assert ok.status_code == 200
    assert ok.json()["citations"][0]["document_name"] == "politica-vacaciones.md"
    assert answered.contexts[0].tenant_id == tenant
    assert str(other) not in ok.text


def test_out_of_domain_abstains_without_calling_the_model() -> None:
    retrieval = _Retrieval(_result())
    response = _ask(
        _principal("member"),
        _service(retrieval, []),
        {"question": "¿Cuál es la capital de Mongolia?"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["abstained"] is True
    assert body["answer"] == PUBLIC_ABSTENTION
    assert body["citations"] == []
    assert retrieval.requests


def test_invalid_question_input_is_422_and_top_k_out_of_range_too() -> None:
    retrieval = _Retrieval(error=InvalidStateError("La pregunta no puede estar vacía"))
    blank = _ask(_principal("member"), _service(retrieval, []), {"question": "   "})
    out_of_range = _ask(
        _principal("member"),
        _service(_Retrieval(_result()), []),
        {"question": "hola", "top_k": 0},
    )

    assert blank.status_code == 422
    assert blank.json()["code"] == "invalid_question"
    assert blank.json()["request_id"]
    assert "   " not in blank.text
    assert out_of_range.status_code == 422


def test_an_invented_citation_is_a_temporary_error_without_the_model_text() -> None:
    response = _ask(
        _principal("member"),
        _service(
            _Retrieval(_result(_evidence())),
            [_json("INSTRUCCION_EJECUTADA", ["chunk-inventado"])],
        ),
        {"question": "¿Cuántos días?"},
    )

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "ungrounded_answer"
    assert "request_id" in body
    assert "answer" not in body
    assert "INSTRUCCION_EJECUTADA" not in response.text
    assert "chunk-inventado" not in response.text


def test_invalid_json_and_timeout_do_not_publish_provider_output() -> None:
    invalid = _ask(
        _principal("member"),
        _service(
            _Retrieval(_result(_evidence())),
            ["SALIDA-INVALIDA-SECRETA", "TAMPOCO-ES-JSON"],
        ),
        {"question": "¿Cuántos días?"},
    )
    timeout = _ask(
        _principal("member"),
        _service(
            _Retrieval(_result(_evidence())),
            [LLMTimeoutError("Ollama no respondió en http://127.0.0.1:11434")],
        ),
        {"question": "¿Cuántos días?"},
    )

    assert invalid.status_code == 503
    assert invalid.json()["code"] == "generation_format_error"
    assert "SALIDA-INVALIDA-SECRETA" not in invalid.text
    assert "TAMPOCO-ES-JSON" not in invalid.text
    assert timeout.status_code == 503
    assert timeout.json()["code"] == "llm_timeout"
    assert "11434" not in timeout.text
    assert timeout.json()["detail"] == "El modelo no respondió a tiempo"


def test_stream_matches_the_validated_post_and_timeout_is_an_error_event() -> None:
    script = [_json("18 días hábiles", ["c1"])]
    evidence = _evidence()
    posted = _ask(
        _principal("member"),
        _service(_Retrieval(_result(evidence)), script),
        {"question": "¿Cuántos días?"},
    )
    streamed = _ask(
        _principal("member"),
        _service(_Retrieval(_result(evidence)), script),
        {"question": "¿Cuántos días?"},
        path="/v1/questions/stream",
    )

    assert posted.status_code == 200
    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("text/event-stream")
    events = _events(streamed.text)
    assert [name for name, _payload in events] == ["status", "answer", "citations", "complete"]
    posted_body = posted.json()
    complete = events[-1][1]
    assert complete["answer"] == posted_body["answer"]
    assert complete["abstained"] == posted_body["abstained"]
    assert complete["citations"] == posted_body["citations"]
    assert complete["pipeline_version"] == posted_body["pipeline_version"]
    assert events[1][1]["answer"] == posted_body["answer"]
    assert events[2][1]["citations"] == posted_body["citations"]
    assert all(_keys(payload).isdisjoint(_FORBIDDEN_MEMBER_KEYS) for _name, payload in events)

    failed = _ask(
        _principal("member"),
        _service(
            _Retrieval(_result(evidence)),
            [LLMTimeoutError("timeout http://127.0.0.1:11434")],
        ),
        {"question": "¿Cuántos días?"},
        path="/v1/questions/stream",
    )
    assert failed.status_code == 503
    error_events = _events(failed.text)
    assert [name for name, _payload in error_events] == ["error"]
    assert error_events[0][1]["code"] == "llm_timeout"
    assert "11434" not in failed.text
    assert "18 días" not in failed.text


def test_an_internal_failure_has_a_stable_code_and_request_id() -> None:
    response = _ask(
        _principal("member"),
        _service(
            _Retrieval(error=RuntimeError(r"C:\secret\PREGUNTA-INTERNA")),
            [],
        ),
        {"question": "PREGUNTA-INTERNA"},
    )

    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "internal_error"
    assert body["detail"] == "No se pudo completar la pregunta"
    assert body["request_id"]
    assert "secret" not in response.text
    assert "PREGUNTA-INTERNA" not in response.text


class _UnknownModel:
    def ensure(self, choice: str | None) -> None:
        del choice
        raise InvalidStateError("El modelo no está disponible")

    def list_models(self) -> tuple[ModelOption, ...]:
        return ()


class _VisibleModels:
    def ensure(self, choice: str | None) -> None:
        del choice

    def list_models(self) -> tuple[ModelOption, ...]:
        return (
            ModelOption(id="ollama:qwen3.5:2b", label="qwen3.5:2b", provider="ollama"),
            ModelOption(id="external", label="modelo-externo", provider="external"),
        )


def test_an_unknown_model_is_rejected_before_retrieval() -> None:
    retrieval = _Retrieval(_result())
    application = _application(_principal("member"), _service(retrieval, []))
    application.state.llm_directory = _UnknownModel()

    with TestClient(application) as client:
        rejected = client.post(
            "/v1/questions",
            json={"question": "hola", "llm": "otro"},
        )
        streamed = client.post(
            "/v1/questions/stream",
            json={"question": "hola", "llm": "otro"},
        )

    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "El modelo no está disponible"
    assert streamed.status_code == 422
    assert retrieval.requests == []


def test_model_list_requires_authentication() -> None:
    application = _application(None, _service(_Retrieval(_result()), []))

    with TestClient(application) as client:
        response = client.get("/v1/models")

    assert response.status_code == 401


def test_model_list_names_models_without_the_external_secret() -> None:
    application = _application(_principal("member"), _service(_Retrieval(_result()), []))
    application.state.llm_directory = _VisibleModels()

    with TestClient(application) as client:
        response = client.get("/v1/models")

    assert response.status_code == 200
    assert response.json()["items"] == [
        {"id": "ollama:qwen3.5:2b", "label": "qwen3.5:2b", "provider": "ollama"},
        {"id": "external", "label": "modelo-externo", "provider": "external"},
    ]
    assert "sk-" not in response.text
    assert "https://" not in response.text


def test_startup_source_does_not_open_the_question_index() -> None:
    main = (repository_root() / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    runtime = (repository_root() / "backend" / "app" / "questions" / "runtime.py").read_text(
        encoding="utf-8"
    )

    assert "ensure_index" not in main
    assert "QdrantVectorStore" not in main
    assert "build_question_service" not in main
    assert "ensure_index" not in runtime
