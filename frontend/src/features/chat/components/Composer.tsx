import { useQuery, useQueryClient, type InfiniteData, type QueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useNavigate } from "react-router";

import {
  asApiError,
  askQuestion,
  createConversation,
  listModels,
  type ApiError,
  type ChatMessage,
  type ConversationDetail,
  type ConversationPage,
  type ConversationSummary,
  type LlmModel,
  type PublicAnswer,
} from "../../../api/http.ts";
import type { Session } from "../../../api/session.ts";
import {
  conversationKey,
  conversationListKey,
  sessionScope,
} from "../hooks/useConversations.ts";
import { beginQuestion, failQuestion, finishQuestion, useQuestionPending } from "../inflight.ts";

const DEFAULT_LLM = "ollama:qwen2.5:7b";

export function Composer({
  session,
  conversationId,
}: {
  session: Session;
  conversationId: string | undefined;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const lock = useRef(false);
  const mounted = useRef(true);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const scope = sessionScope(session);
  const pending = useQuestionPending(conversationId);
  const models = useQuery({
    queryKey: ["llm-models", scope],
    queryFn: listModels,
  });
  const options = models.data ?? [];
  const selected = selectedModel(options, picked);
  const blocked = busy || pending.pending;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  function submit() {
    if (lock.current || pending.pending) {
      return;
    }
    const question = draft.trim();
    if (question.length === 0) {
      return;
    }
    lock.current = true;
    setBusy(true);
    setError(null);
    const idempotencyKey = crypto.randomUUID();
    const llm = selected.length > 0 ? selected : undefined;
    void runQuestion({
      question,
      conversationId,
      idempotencyKey,
      llm,
      scope,
      queryClient,
      navigate,
      mounted,
      setDraft,
      setError,
      setBusy,
      unlock: () => {
        lock.current = false;
      },
    });
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submit();
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <form onSubmit={onSubmit} aria-busy={blocked}>
      <label htmlFor="llm">Modelo</label>
      <select
        id="llm"
        name="llm"
        value={selected}
        disabled={blocked || models.isPending || options.length === 0}
        onChange={(event) => setPicked(event.target.value)}
      >
        {options.length === 0 ? <option value="">Cargando modelos</option> : null}
        {options.map((item) => (
          <option key={item.id} value={item.id}>
            {item.provider === "external" ? `API externa (${item.label})` : item.label}
          </option>
        ))}
      </select>
      {models.isError ? <p role="alert">{asApiError(models.error).message}</p> : null}
      <label htmlFor="question">Pregunta</label>
      <textarea
        id="question"
        name="question"
        rows={3}
        value={draft}
        disabled={blocked}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={onKeyDown}
      />
      <button type="submit" disabled={blocked}>
        Enviar
      </button>
      {blocked ? <p role="status">Procesando la pregunta</p> : null}
      {error ? <p role="alert">{visibleFailure(error)}</p> : null}
    </form>
  );
}

const TITLE_CHARS = 80;

async function runQuestion(input: {
  question: string;
  conversationId: string | undefined;
  idempotencyKey: string;
  llm: string | undefined;
  scope: string;
  queryClient: QueryClient;
  navigate: (path: string) => void;
  mounted: { current: boolean };
  setDraft: (value: string) => void;
  setError: (error: ApiError | null) => void;
  setBusy: (value: boolean) => void;
  unlock: () => void;
}): Promise<void> {
  let activeId = input.conversationId;
  try {
    if (activeId === undefined) {
      const created = await createConversation(input.question.slice(0, TITLE_CHARS));
      activeId = created.id;
      rememberCreated(input.queryClient, input.scope, created);
      beginQuestion(activeId);
      input.navigate(`/conversations/${activeId}`);
    } else {
      beginQuestion(activeId);
    }
    const result = await askQuestion({
      question: input.question,
      conversationId: activeId,
      idempotencyKey: input.idempotencyKey,
      llm: input.llm,
    });
    await input.queryClient.cancelQueries({
      queryKey: conversationKey(input.scope, result.conversationId),
    });
    rememberAnswer(input.queryClient, input.scope, input.question, result, activeId);
    void input.queryClient.invalidateQueries({ queryKey: conversationListKey(input.scope) });
    void input.queryClient.invalidateQueries({
      queryKey: conversationKey(input.scope, result.conversationId),
    });
    finishQuestion(activeId);
    if (input.mounted.current) {
      input.setDraft("");
    }
  } catch (caught: unknown) {
    const failure = asApiError(caught);
    if (activeId !== undefined) {
      failQuestion(activeId, visibleFailure(failure));
    } else if (input.mounted.current) {
      input.setError(failure);
    }
  } finally {
    input.unlock();
    if (input.mounted.current) {
      input.setBusy(false);
    }
  }
}

function rememberCreated(
  queryClient: QueryClient,
  scope: string,
  created: ConversationSummary,
): void {
  queryClient.setQueryData<InfiniteData<ConversationPage>>(conversationListKey(scope), (current) => {
    const item = { id: created.id, title: created.title };
    if (current === undefined) {
      return { pages: [{ items: [item], nextCursor: null }], pageParams: [null] };
    }
    if (current.pages.some((page) => page.items.some((entry) => entry.id === item.id))) {
      return current;
    }
    const [first, ...rest] = current.pages;
    if (first === undefined) {
      return { ...current, pages: [{ items: [item], nextCursor: null }] };
    }
    return { ...current, pages: [{ ...first, items: [item, ...first.items] }, ...rest] };
  });
}

function visibleFailure(failure: ApiError): string {
  if (failure.status >= 500) {
    return `${failure.message} Identificador: ${failure.requestId}`;
  }
  return failure.message;
}

function selectedModel(options: LlmModel[], picked: string | null): string {
  if (picked !== null && options.some((item) => item.id === picked)) {
    return picked;
  }
  return options.find((item) => item.id === DEFAULT_LLM)?.id ?? options[0]?.id ?? "";
}

function rememberAnswer(
  queryClient: QueryClient,
  scope: string,
  question: string,
  result: PublicAnswer,
  currentId: string | undefined,
): void {
  const conversationId = currentId ?? result.conversationId;
  const userMessage: ChatMessage = {
    id: `local-${result.requestId}`,
    role: "user",
    content: question,
    citations: [],
    abstained: false,
    feedbackRating: null,
  };
  const assistant: ChatMessage = {
    id: result.messageId,
    role: "assistant",
    content: result.answer,
    citations: result.abstained ? [] : result.citations,
    abstained: result.abstained,
    feedbackRating: null,
  };
  queryClient.setQueryData<ConversationDetail>(
    conversationKey(scope, conversationId),
    (current) => {
      const alreadyThere = current?.messages.some((item) => item.id === assistant.id) === true;
      if (current !== undefined && alreadyThere) {
        return current;
      }
      const messages =
        current === undefined ? [userMessage, assistant] : [...current.messages, userMessage, assistant];
      return {
        id: conversationId,
        title: current?.title ?? "Conversación",
        messages,
      };
    },
  );
}
