import { HttpResponse } from "msw";

export const ACCESS_TOKEN = "rag-access-token-prueba";
export const OTHER_TOKEN = "rag-access-token-bea";
export const USER_A = "66666666-6666-6666-6666-666666666666";
export const USER_B = "88888888-8888-8888-8888-888888888888";
export const TENANT = "77777777-7777-7777-7777-777777777777";
export const CONVERSATION_ID = "22222222-2222-2222-2222-222222222222";
export const MESSAGE_ID = "33333333-3333-3333-3333-333333333333";
export const REQUEST_ID = "11111111-1111-1111-1111-111111111111";
export const PLANTED_SCORE = "0.857999";

export const CITATION = {
  document_id: "55555555-5555-5555-5555-555555555555",
  document_name: "Política de vacaciones",
  page: 2,
  section: "Días hábiles",
  excerpt: "18 días hábiles",
};

export const ANSWER = "La política concede 18 días hábiles.";

export function sessionBody(userId: string, accessToken = ACCESS_TOKEN) {
  return {
    access_token: accessToken,
    token_type: "bearer",
    expires_in: 900,
    principal_type: "member",
    user_id: userId,
    tenant_id: TENANT,
  };
}

export function emptyConversations() {
  return { items: [], next_cursor: null, total: 0 };
}

export function conversationPage(title: string, id = CONVERSATION_ID) {
  return {
    items: [
      {
        id,
        title,
        history_scope: "visual",
        created_at: "2026-10-01T12:00:00Z",
        updated_at: "2026-10-01T12:00:01Z",
        expires_at: "2026-10-31T12:00:00Z",
      },
    ],
    next_cursor: null,
    total: 1,
  };
}

export function questionPayload(options?: {
  abstained?: boolean;
  answer?: string;
  excerpt?: string;
}) {
  const abstained = options?.abstained === true;
  return {
    request_id: REQUEST_ID,
    answer: options?.answer ?? ANSWER,
    abstained,
    citations: [
      {
        ...CITATION,
        excerpt: options?.excerpt ?? CITATION.excerpt,
      },
    ],
    pipeline_version: "prompt-v4",
    conversation_id: CONVERSATION_ID,
    message_id: MESSAGE_ID,
    history_scope: "visual",
    debug: {
      request_id: REQUEST_ID,
      embedding_version: "e5",
      index_version: "v1",
      hits_retrieved: 1,
      hits_after_validation: 1,
      hits_after_threshold: abstained ? 0 : 1,
      returned: abstained ? 0 : 1,
      rounds: 1,
      top_k: 5,
      min_retrieval_score: Number(PLANTED_SCORE),
      embed_ms: 1,
      search_ms: 1,
      validate_ms: 1,
      generation_ms: 1,
      llm_called: !abstained,
      token_estimate: 10,
      evidence: [
        {
          chunk_id: "chunk-1",
          document_id: CITATION.document_id,
          score: Number(PLANTED_SCORE),
          position: 0,
        },
      ],
    },
  };
}

export function conversationDetail(question: string, payload = questionPayload()) {
  return {
    id: payload.conversation_id,
    title: "Vacaciones",
    history_scope: "visual",
    created_at: "2026-10-01T12:00:00Z",
    updated_at: "2026-10-01T12:00:01Z",
    expires_at: "2026-10-31T12:00:00Z",
    messages: [
      {
        id: "44444444-4444-4444-4444-444444444444",
        request_id: payload.request_id,
        role: "user",
        content: question,
        citations: [],
        abstained: null,
        pipeline_version: null,
        created_at: "2026-10-01T12:00:00Z",
      },
      {
        id: payload.message_id,
        request_id: payload.request_id,
        role: "assistant",
        content: payload.answer,
        citations: payload.citations,
        abstained: payload.abstained,
        pipeline_version: payload.pipeline_version,
        created_at: "2026-10-01T12:00:01Z",
      },
    ],
  };
}

export function sseResponse(payload: ReturnType<typeof questionPayload>, status = 200) {
  const events = [
    { event: "status", data: { request_id: payload.request_id, stage: "validated" } },
    {
      event: "answer",
      data: {
        request_id: payload.request_id,
        answer: payload.answer,
        abstained: payload.abstained,
      },
    },
    {
      event: "citations",
      data: { request_id: payload.request_id, citations: payload.citations },
    },
    { event: "complete", data: payload },
  ];
  const body = events
    .map((item) => `event: ${item.event}\ndata: ${JSON.stringify(item.data)}\n\n`)
    .join("");
  return new HttpResponse(body, {
    status,
    headers: { "Content-Type": "text/event-stream" },
  });
}
