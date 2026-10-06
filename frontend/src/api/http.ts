/** Único acceso HTTP. Renueva la sesión una vez y no escribe tokens en la consola. */

import type { components } from "./generated/schema.ts";
import { getSession, resetSessionForTests, setSession, type Session } from "./session.ts";

export type Citation = {
  documentId: string;
  documentName: string;
  page: number;
  section: string | null;
  excerpt: string;
};

export type ConversationSummary = {
  id: string;
  title: string;
};

export type ConversationPage = {
  items: ConversationSummary[];
  nextCursor: string | null;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  abstained: boolean;
  feedbackRating: FeedbackRating | null;
};

export type ConversationDetail = {
  id: string;
  title: string;
  messages: ChatMessage[];
};

export type PublicAnswer = {
  requestId: string;
  answer: string;
  abstained: boolean;
  citations: Citation[];
  pipelineVersion: string;
  conversationId: string;
  messageId: string;
  historyScope: string;
};

export type FeedbackRating = "helpful" | "not_helpful";

export type SavedFeedback = {
  messageId: string;
  rating: FeedbackRating;
};

export type LoginInput = {
  email: string;
  password: string;
  tenantSlug: string;
};

export type AskInput = {
  question: string;
  conversationId?: string;
  idempotencyKey: string;
  llm?: string;
};

export type LlmModel = {
  id: string;
  label: string;
  provider: "ollama" | "external";
};

const DEFAULT_QUESTION_TIMEOUT_MS = 180_000;
const REQUEST_TIMEOUT_MS = 30_000;

let questionTimeoutMs = DEFAULT_QUESTION_TIMEOUT_MS;
let refreshInFlight: Promise<Session | null> | null = null;
let bootPromise: Promise<Session | null> | null = null;

export class ApiError extends Error {
  readonly status: number;
  readonly requestId: string;
  readonly retryAfterSeconds: number | null;
  readonly code: string | null;

  constructor(
    message: string,
    status: number,
    requestId: string,
    retryAfterSeconds: number | null,
    code: string | null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.requestId = requestId;
    this.retryAfterSeconds = retryAfterSeconds;
    this.code = code;
  }
}

class StreamUnavailableError extends Error {
  constructor() {
    super("stream-unavailable");
    this.name = "StreamUnavailableError";
  }
}

export function asApiError(error: unknown): ApiError {
  if (error instanceof ApiError) {
    return error;
  }
  return new ApiError(
    "No se pudo completar la solicitud.",
    0,
    crypto.randomUUID(),
    null,
    null,
  );
}

export function restoreSession(): Promise<Session | null> {
  if (bootPromise === null) {
    bootPromise = refreshSession();
  }
  return bootPromise;
}

export async function login(input: LoginInput): Promise<Session> {
  const requestId = crypto.randomUUID();
  const body: components["schemas"]["LoginRequest"] = {
    email: input.email,
    password: input.password,
  };
  if (input.tenantSlug.trim().length > 0) {
    body.tenant_slug = input.tenantSlug.trim();
  }
  const response = await fetch("/v1/auth/login", {
    method: "POST",
    credentials: "include",
    headers: jsonHeaders(requestId),
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw await toApiError(response, requestId);
  }
  const next = parseSession(await response.json(), requestId);
  setSession(next);
  return next;
}

export async function logoutRequest(): Promise<void> {
  const requestId = crypto.randomUUID();
  try {
    await fetch("/v1/auth/logout", {
      method: "POST",
      credentials: "include",
      headers: {
        Accept: "application/json",
        "X-Request-ID": requestId,
        ...csrfHeaders(),
      },
    });
  } catch {
    // La sesión local ya se vació. Un fallo de red no la restaura.
  }
}

export function listConversations(cursor: string | null): Promise<ConversationPage> {
  const params = new URLSearchParams({ limit: "50" });
  if (cursor !== null) {
    params.set("cursor", cursor);
  }
  return requestJson(`/v1/conversations?${params.toString()}`, { method: "GET" }, parsePage);
}

export function createConversation(title: string): Promise<ConversationSummary> {
  const body: components["schemas"]["ConversationCreateBody"] = { title };
  return requestJson("/v1/conversations", { method: "POST", body }, parseSummary);
}

export function getConversation(conversationId: string): Promise<ConversationDetail> {
  return requestJson(`/v1/conversations/${conversationId}`, { method: "GET" }, parseDetail);
}

export function renameConversation(
  conversationId: string,
  title: string,
): Promise<ConversationSummary> {
  const body: components["schemas"]["ConversationUpdateBody"] = { title };
  return requestJson(
    `/v1/conversations/${conversationId}`,
    { method: "PATCH", body },
    parseSummary,
  );
}

export function deleteConversation(conversationId: string): Promise<void> {
  return requestEmpty(`/v1/conversations/${conversationId}`, { method: "DELETE" });
}

export function listModels(): Promise<LlmModel[]> {
  return requestJson("/v1/models", { method: "GET" }, parseModelList);
}

export function submitFeedback(
  messageId: string,
  rating: FeedbackRating,
): Promise<SavedFeedback> {
  const body: components["schemas"]["FeedbackBody"] = { rating };
  return requestJson(`/v1/messages/${messageId}/feedback`, { method: "PUT", body }, parseFeedback);
}

export async function askQuestion(input: AskInput): Promise<PublicAnswer> {
  try {
    return await askStream(input, false);
  } catch (error) {
    if (error instanceof StreamUnavailableError) {
      return askJson(input);
    }
    throw error;
  }
}

export function resetClientForTests(): void {
  resetSessionForTests();
  refreshInFlight = null;
  bootPromise = null;
  questionTimeoutMs = DEFAULT_QUESTION_TIMEOUT_MS;
}

export function setQuestionTimeoutForTests(timeoutMs: number): void {
  questionTimeoutMs = timeoutMs;
}

function refreshSession(): Promise<Session | null> {
  if (refreshInFlight === null) {
    refreshInFlight = performRefresh().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

async function performRefresh(): Promise<Session | null> {
  const requestId = crypto.randomUUID();
  let response: Response;
  try {
    response = await fetch("/v1/auth/refresh", {
      method: "POST",
      credentials: "include",
      headers: {
        Accept: "application/json",
        "X-Request-ID": requestId,
        ...csrfHeaders(),
      },
    });
  } catch (error) {
    throw networkError(error, requestId);
  }
  if (response.status === 401) {
    setSession(null);
    return null;
  }
  if (!response.ok) {
    setSession(null);
    throw await toApiError(response, requestId);
  }
  const next = parseSession(await response.json(), requestId);
  setSession(next);
  return next;
}

export async function requestJson<T>(
  path: string,
  init: { method: "GET" | "POST" | "PUT" | "PATCH"; body?: unknown },
  parse: (value: unknown, requestId: string) => T,
  retried = false,
): Promise<T> {
  const requestId = crypto.randomUUID();
  const response = await send(path, init, requestId, REQUEST_TIMEOUT_MS);
  if (response.status === 401 && !retried) {
    const refreshed = await refreshSession();
    if (refreshed !== null) {
      return requestJson(path, init, parse, true);
    }
  }
  if (!response.ok) {
    throw await toApiError(response, requestId);
  }
  return parse(await response.json(), requestId);
}

export async function requestEmpty(
  path: string,
  init: { method: "DELETE" },
  retried = false,
): Promise<void> {
  const requestId = crypto.randomUUID();
  const response = await send(path, init, requestId, REQUEST_TIMEOUT_MS);
  if (response.status === 401 && !retried) {
    const refreshed = await refreshSession();
    if (refreshed !== null) {
      return requestEmpty(path, init, true);
    }
  }
  if (!response.ok) {
    throw await toApiError(response, requestId);
  }
}

export function uploadFile(
  path: string,
  file: File,
  onProgress: (loaded: number, total: number) => void,
): Promise<unknown> {
  return uploadOnce(path, file, onProgress, false);
}

function uploadOnce(
  path: string,
  file: File,
  onProgress: (loaded: number, total: number) => void,
  retried: boolean,
): Promise<unknown> {
  const requestId = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    xhr.timeout = REQUEST_TIMEOUT_MS;
    xhr.setRequestHeader("Accept", "application/json");
    xhr.setRequestHeader("X-Request-ID", requestId);
    const token = getSession()?.accessToken;
    if (token !== undefined && token.length > 0) {
      xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    }
    xhr.upload.onprogress = (event) => {
      const total = event.lengthComputable ? event.total : file.size;
      onProgress(event.loaded, total);
    };
    xhr.onerror = () => {
      reject(networkError(new Error("xhr"), requestId));
    };
    xhr.ontimeout = () => {
      reject(
        new ApiError(
          "La carga tardó demasiado. Inténtalo de nuevo.",
          0,
          requestId,
          null,
          "timeout",
        ),
      );
    };
    xhr.onload = () => {
      void (async () => {
        const response = new Response(xhr.responseText, { status: xhr.status });
        if (xhr.status === 401 && !retried) {
          const refreshed = await refreshSession();
          if (refreshed !== null) {
            resolve(await uploadOnce(path, file, onProgress, true));
            return;
          }
        }
        if (xhr.status < 200 || xhr.status >= 300) {
          reject(await toApiError(response, requestId));
          return;
        }
        try {
          resolve(JSON.parse(xhr.responseText) as unknown);
        } catch {
          reject(invalid(requestId));
        }
      })().catch(reject);
    };
    const body = new FormData();
    body.append("file", file, file.name);
    xhr.send(body);
  });
}

async function askJson(input: AskInput, retried = false): Promise<PublicAnswer> {
  const requestId = crypto.randomUUID();
  const response = await send(
    "/v1/questions",
    { method: "POST", body: questionBody(input), idempotencyKey: input.idempotencyKey },
    requestId,
    questionTimeoutMs,
  );
  if (response.status === 401 && !retried) {
    const refreshed = await refreshSession();
    if (refreshed !== null) {
      return askJson(input, true);
    }
  }
  if (!response.ok) {
    throw await toApiError(response, requestId);
  }
  return parseAnswer(await response.json(), requestId);
}

async function askStream(input: AskInput, retried: boolean): Promise<PublicAnswer> {
  const requestId = crypto.randomUUID();
  const timed = linkedSignal(questionTimeoutMs);
  let response: Response;
  try {
    response = await fetch("/v1/questions/stream", {
      method: "POST",
      credentials: "omit",
      signal: timed.signal,
      headers: authHeaders(requestId, true, input.idempotencyKey),
      body: JSON.stringify(questionBody(input)),
    });
  } catch (error) {
    if (timed.timedOut()) {
      throw timeoutError(requestId);
    }
    if (error instanceof TypeError) {
      throw new StreamUnavailableError();
    }
    throw error;
  }
  if (response.status === 401 && !retried) {
    const refreshed = await refreshSession();
    if (refreshed !== null) {
      return askStream(input, true);
    }
  }
  const contentType = response.headers.get("content-type") ?? "";
  if (!response.ok) {
    if (response.status === 404 || response.status === 405) {
      throw new StreamUnavailableError();
    }
    if (contentType.includes("text/event-stream")) {
      throw await readStreamError(response, requestId);
    }
    throw await toApiError(response, requestId);
  }
  if (!contentType.includes("text/event-stream")) {
    throw new StreamUnavailableError();
  }
  return readValidatedAnswer(response, requestId);
}

async function send(
  path: string,
  init: { method: string; body?: unknown; idempotencyKey?: string },
  requestId: string,
  timeoutMs: number,
): Promise<Response> {
  const timed = linkedSignal(timeoutMs);
  try {
    return await fetch(path, {
      method: init.method,
      credentials: "omit",
      signal: timed.signal,
      headers: authHeaders(requestId, init.body !== undefined, init.idempotencyKey),
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
    });
  } catch (error) {
    if (timed.timedOut()) {
      throw timeoutError(requestId);
    }
    throw networkError(error, requestId);
  }
}

function questionBody(input: AskInput): components["schemas"]["QuestionRequestBody"] {
  const body: components["schemas"]["QuestionRequestBody"] = { question: input.question };
  if (input.conversationId !== undefined) {
    body.conversation_id = input.conversationId;
  }
  if (input.llm !== undefined && input.llm.length > 0) {
    body.llm = input.llm;
  }
  return body;
}

function authHeaders(
  requestId: string,
  json: boolean,
  idempotencyKey?: string,
): Record<string, string> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    "X-Request-ID": requestId,
  };
  if (json) {
    headers["Content-Type"] = "application/json";
  }
  if (idempotencyKey !== undefined) {
    headers["Idempotency-Key"] = idempotencyKey;
  }
  const token = getSession()?.accessToken;
  if (token !== undefined && token.length > 0) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

function csrfHeaders(): Record<string, string> {
  const prefix = "rag_csrf=";
  for (const part of document.cookie.split(";")) {
    const piece = part.trim();
    if (piece.startsWith(prefix)) {
      return { "X-CSRF-Token": decodeURIComponent(piece.slice(prefix.length)) };
    }
  }
  return {};
}

function jsonHeaders(requestId: string): Record<string, string> {
  return {
    Accept: "application/json",
    "Content-Type": "application/json",
    "X-Request-ID": requestId,
  };
}

function linkedSignal(timeoutMs: number): { signal: AbortSignal; timedOut: () => boolean } {
  const timeout = AbortSignal.timeout(timeoutMs);
  return { signal: timeout, timedOut: () => timeout.aborted };
}

function timeoutError(requestId: string): ApiError {
  return new ApiError(
    "La pregunta tardó demasiado. Inténtalo de nuevo.",
    0,
    requestId,
    null,
    "timeout",
  );
}

function networkError(error: unknown, requestId: string): ApiError {
  if (error instanceof ApiError) {
    return error;
  }
  return new ApiError("No hay conexión con el servidor.", 0, requestId, null, "network");
}

function invalid(requestId: string): ApiError {
  return new ApiError(
    "La respuesta del servidor no tiene la forma esperada.",
    500,
    requestId,
    null,
    "invalid_response",
  );
}

async function toApiError(response: Response, requestId: string): Promise<ApiError> {
  const retryAfterSeconds = parseRetryAfter(response.headers.get("Retry-After"));
  let detail = "No se pudo completar la solicitud.";
  let code: string | null = null;
  let bodyRequestId = requestId;
  const text = await response.text();
  if (text.length > 0) {
    try {
      const parsed: unknown = JSON.parse(text);
      if (isRecord(parsed)) {
        if (typeof parsed.detail === "string" && parsed.detail.length > 0) {
          detail = parsed.detail;
        } else if (Array.isArray(parsed.detail)) {
          detail = "Los datos enviados no son válidos.";
        }
        if (typeof parsed.request_id === "string" && parsed.request_id.length > 0) {
          bodyRequestId = parsed.request_id;
        }
        if (typeof parsed.code === "string") {
          code = parsed.code;
        }
      }
    } catch {
      detail = "No se pudo completar la solicitud.";
    }
  }
  if (response.status === 403) {
    detail = "No tienes permiso para esta acción.";
  }
  if (response.status === 429) {
    const wait = retryAfterSeconds === null ? "un momento" : `${retryAfterSeconds} segundos`;
    detail = `Demasiadas solicitudes. Espera ${wait} antes de intentar otra vez.`;
  }
  if (response.status >= 500) {
    detail = "No se pudo completar la solicitud. Comunica este identificador a soporte.";
  }
  return new ApiError(detail, response.status, bodyRequestId, retryAfterSeconds, code);
}

function parseRetryAfter(header: string | null): number | null {
  if (header === null || header.trim().length === 0) {
    return null;
  }
  const seconds = Number(header);
  if (Number.isFinite(seconds) && seconds >= 0) {
    return Math.ceil(seconds);
  }
  const date = Date.parse(header);
  if (Number.isNaN(date)) {
    return null;
  }
  return Math.max(0, Math.ceil((date - Date.now()) / 1000));
}

async function readValidatedAnswer(response: Response, requestId: string): Promise<PublicAnswer> {
  const reader = response.body?.getReader();
  if (reader === undefined) {
    throw new StreamUnavailableError();
  }
  const decoder = new TextDecoder();
  let buffer = "";
  let sawEvent = false;
  let complete: PublicAnswer | null = null;
  const consume = (block: string): void => {
    const parsed = parseEventBlock(block, requestId);
    if (parsed === null) {
      return;
    }
    sawEvent = true;
    if (parsed.event === "error") {
      throw streamFailure(parsed.data, requestId, 500);
    }
    if (parsed.event === "complete") {
      complete = parseAnswer(parsed.data, requestId);
    }
  };
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) {
        break;
      }
      buffer += decoder.decode(chunk.value, { stream: true });
      const parts = buffer.split(/\r?\n\r?\n/);
      buffer = parts.pop() ?? "";
      for (const part of parts) {
        consume(part);
      }
    }
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    if (error instanceof DOMException && error.name === "AbortError") {
      throw timeoutError(requestId);
    }
    throw invalid(requestId);
  }
  if (buffer.trim().length > 0) {
    consume(buffer);
  }
  if (complete !== null) {
    return complete;
  }
  if (sawEvent) {
    throw new ApiError("La respuesta se interrumpió.", 0, requestId, null, "interrupted");
  }
  throw new StreamUnavailableError();
}

async function readStreamError(response: Response, requestId: string): Promise<ApiError> {
  const text = await response.text();
  const blocks = text.split(/\r?\n\r?\n/);
  for (const block of blocks) {
    try {
      const parsed = parseEventBlock(block, requestId);
      if (parsed?.event === "error") {
        return streamFailure(parsed.data, requestId, response.status);
      }
    } catch (error) {
      if (error instanceof ApiError) {
        return error;
      }
    }
  }
  return new ApiError(
    "No se pudo completar la solicitud. Comunica este identificador a soporte.",
    response.status,
    requestId,
    null,
    "internal_error",
  );
}

function streamFailure(value: unknown, requestId: string, status: number): ApiError {
  let bodyRequestId = requestId;
  let code = "internal_error";
  let detail = "No se pudo completar la solicitud. Comunica este identificador a soporte.";
  if (isRecord(value)) {
    if (typeof value.request_id === "string" && value.request_id.length > 0) {
      bodyRequestId = value.request_id;
    }
    if (typeof value.code === "string" && value.code.length > 0) {
      code = value.code;
    }
    if (typeof value.detail === "string" && value.detail.trim().length > 0) {
      detail = value.detail.trim();
    }
  }
  return new ApiError(detail, status, bodyRequestId, null, code);
}

function parseEventBlock(
  block: string,
  requestId: string,
): { event: string; data: unknown } | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trim());
    }
  }
  if (dataLines.length === 0) {
    return null;
  }
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) as unknown };
  } catch {
    throw invalid(requestId);
  }
}

function parseSession(value: unknown, requestId: string): Session {
  if (!isRecord(value)) {
    throw invalid(requestId);
  }
  return {
    accessToken: readString(value, "access_token", requestId),
    expiresIn: readNumber(value, "expires_in", requestId),
    principalType: readString(value, "principal_type", requestId),
    userId: readNullableString(value, "user_id", requestId),
    tenantId: readNullableString(value, "tenant_id", requestId),
  };
}

function parsePage(value: unknown, requestId: string): ConversationPage {
  if (!isRecord(value) || !Array.isArray(value.items)) {
    throw invalid(requestId);
  }
  return {
    items: value.items.map((item) => parseSummary(item, requestId)),
    nextCursor: readNullableString(value, "next_cursor", requestId),
  };
}

function parseSummary(value: unknown, requestId: string): ConversationSummary {
  if (!isRecord(value)) {
    throw invalid(requestId);
  }
  return {
    id: readString(value, "id", requestId),
    title: readString(value, "title", requestId),
  };
}

function parseDetail(value: unknown, requestId: string): ConversationDetail {
  if (!isRecord(value) || !Array.isArray(value.messages)) {
    throw invalid(requestId);
  }
  return {
    id: readString(value, "id", requestId),
    title: readString(value, "title", requestId),
    messages: value.messages.map((item) => parseMessage(item, requestId)),
  };
}

function parseMessage(value: unknown, requestId: string): ChatMessage {
  if (!isRecord(value)) {
    throw invalid(requestId);
  }
  const role = readString(value, "role", requestId);
  if (role !== "user" && role !== "assistant") {
    throw invalid(requestId);
  }
  const abstained = value.abstained;
  if (abstained !== null && typeof abstained !== "boolean") {
    throw invalid(requestId);
  }
  return {
    id: readString(value, "id", requestId),
    role,
    content: readString(value, "content", requestId),
    citations: readCitations(value.citations, requestId),
    abstained: abstained === true,
    feedbackRating: readFeedbackRating(value.feedback_rating, requestId),
  };
}

function readFeedbackRating(value: unknown, requestId: string): FeedbackRating | null {
  if (value === undefined || value === null) {
    return null;
  }
  if (value === "helpful" || value === "not_helpful") {
    return value;
  }
  throw invalid(requestId);
}

function parseAnswer(value: unknown, requestId: string): PublicAnswer {
  if (!isRecord(value)) {
    throw invalid(requestId);
  }
  const abstained = value.abstained;
  if (typeof abstained !== "boolean") {
    throw invalid(requestId);
  }
  return {
    requestId: readString(value, "request_id", requestId),
    answer: readString(value, "answer", requestId),
    abstained,
    citations: readCitations(value.citations, requestId),
    pipelineVersion: readString(value, "pipeline_version", requestId),
    conversationId: readString(value, "conversation_id", requestId),
    messageId: readString(value, "message_id", requestId),
    historyScope: readString(value, "history_scope", requestId),
  };
}

function parseFeedback(value: unknown, requestId: string): SavedFeedback {
  if (!isRecord(value)) {
    throw invalid(requestId);
  }
  const rating = readString(value, "rating", requestId);
  if (rating !== "helpful" && rating !== "not_helpful") {
    throw invalid(requestId);
  }
  return {
    messageId: readString(value, "message_id", requestId),
    rating,
  };
}

function readCitations(value: unknown, requestId: string): Citation[] {
  if (!Array.isArray(value)) {
    throw invalid(requestId);
  }
  return value.map((item) => {
    if (!isRecord(item)) {
      throw invalid(requestId);
    }
    const documentId = readString(item, "document_id", requestId);
    const page = item.page;
    if (typeof page !== "number" || !Number.isFinite(page)) {
      throw invalid(requestId);
    }
    return {
      documentId,
      documentName: readString(item, "document_name", requestId),
      page,
      section: readNullableString(item, "section", requestId),
      excerpt: readString(item, "excerpt", requestId),
    };
  });
}

function readString(record: Record<string, unknown>, key: string, requestId: string): string {
  const value = record[key];
  if (typeof value !== "string") {
    throw invalid(requestId);
  }
  return value;
}

function readNullableString(
  record: Record<string, unknown>,
  key: string,
  requestId: string,
): string | null {
  const value = record[key];
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value !== "string") {
    throw invalid(requestId);
  }
  return value;
}

function readNumber(record: Record<string, unknown>, key: string, requestId: string): number {
  const value = record[key];
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw invalid(requestId);
  }
  return value;
}

function parseModelList(value: unknown, requestId: string): LlmModel[] {
  if (!isRecord(value) || !Array.isArray(value.items)) {
    throw invalid(requestId);
  }
  return value.items.map((item) => parseModel(item, requestId));
}

function parseModel(value: unknown, requestId: string): LlmModel {
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    value.id.length === 0 ||
    typeof value.label !== "string" ||
    value.label.length === 0 ||
    (value.provider !== "ollama" && value.provider !== "external")
  ) {
    throw invalid(requestId);
  }
  return { id: value.id, label: value.label, provider: value.provider };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
