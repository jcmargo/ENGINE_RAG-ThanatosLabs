import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { setQuestionTimeoutForTests } from "../../../api/http.ts";
import { server } from "../../../test/server.ts";
import {
  ANSWER,
  CITATION,
  CONVERSATION_ID,
  MESSAGE_ID,
  PLANTED_SCORE,
  USER_A,
  conversationDetail,
  conversationPage,
  emptyConversations,
  questionPayload,
  sessionBody,
  sseResponse,
} from "../../../test/fixtures.ts";
import { renderApp } from "../../../test/render.tsx";

const QUESTION = "¿Cuántos días de vacaciones hay?";

function authenticate() {
  server.use(
    http.post("/v1/auth/refresh", () => HttpResponse.json(sessionBody(USER_A))),
    http.get("/v1/conversations", () => HttpResponse.json(emptyConversations())),
    http.get(`/v1/conversations/${CONVERSATION_ID}`, () =>
      HttpResponse.json({
        id: CONVERSATION_ID,
        title: "Conversación",
        history_scope: "visual",
        created_at: "2026-10-01T12:00:00Z",
        updated_at: "2026-10-01T12:00:00Z",
        expires_at: "2026-10-31T12:00:00Z",
        messages: [],
      }),
    ),
    http.post("/v1/conversations", async ({ request }) => {
      const body = (await request.json()) as { title?: string };
      return HttpResponse.json(
        {
          id: CONVERSATION_ID,
          title: body.title ?? "Conversación",
          history_scope: "visual",
          created_at: "2026-10-01T12:00:00Z",
          updated_at: "2026-10-01T12:00:00Z",
          expires_at: "2026-10-31T12:00:00Z",
        },
        { status: 201 },
      );
    }),
  );
}

test("muestra la respuesta y una cita expandible sin el score", async () => {
  const user = userEvent.setup();
  const payload = questionPayload();
  authenticate();
  server.use(
    http.post("/v1/questions/stream", () => sseResponse(payload)),
    http.get(`/v1/conversations/${CONVERSATION_ID}`, () =>
      HttpResponse.json(conversationDetail(QUESTION, payload)),
    ),
  );
  renderApp("/");

  await user.type(await screen.findByLabelText("Pregunta"), QUESTION);
  await user.click(screen.getByRole("button", { name: "Enviar" }));

  expect(await screen.findByText(ANSWER)).toBeVisible();
  expect(screen.queryByText(PLANTED_SCORE)).not.toBeInTheDocument();
  await user.click(
    screen.getByText(`Cita 1: ${CITATION.document_name}, página 2, sección ${CITATION.section}`),
  );
  expect(screen.getByText(CITATION.excerpt)).toBeVisible();
  expect(
    screen.getByText(
      "Un seguimiento usa tu pregunta anterior solo para buscar. La respuesta sale de los documentos.",
    ),
  ).toBeVisible();
});

test("la abstención no inventa tarjetas de fuente", async () => {
  const user = userEvent.setup();
  const payload = questionPayload({
    abstained: true,
    answer: "No está en los documentos disponibles",
    excerpt: "CITA-QUE-NO-DEBE-VERSE",
  });
  authenticate();
  server.use(
    http.post("/v1/questions/stream", () => sseResponse(payload)),
    http.get(`/v1/conversations/${CONVERSATION_ID}`, () =>
      HttpResponse.json(conversationDetail(QUESTION, payload)),
    ),
  );
  renderApp("/");

  await user.type(await screen.findByLabelText("Pregunta"), QUESTION);
  await user.click(screen.getByRole("button", { name: "Enviar" }));

  expect(await screen.findByText("Sin evidencia suficiente")).toBeVisible();
  expect(screen.getByText("No está en los documentos disponibles")).toBeVisible();
  expect(screen.queryByText("CITA-QUE-NO-DEBE-VERSE")).not.toBeInTheDocument();
  expect(screen.queryByText(/Cita 1:/)).not.toBeInTheDocument();
});

test("un doble clic no envía dos preguntas", async () => {
  const user = userEvent.setup();
  let calls = 0;
  const payload = questionPayload();
  authenticate();
  server.use(
    http.post("/v1/questions/stream", async () => {
      calls += 1;
      await delay(250);
      return sseResponse(payload);
    }),
    http.get(`/v1/conversations/${CONVERSATION_ID}`, () =>
      HttpResponse.json(conversationDetail(QUESTION, payload)),
    ),
  );
  renderApp("/");

  await user.type(await screen.findByLabelText("Pregunta"), QUESTION);
  const send = screen.getByRole("button", { name: "Enviar" });
  await user.click(send);
  await user.click(send);

  expect(calls).toBe(1);
  expect(await screen.findByText(ANSWER)).toBeVisible();
  expect(calls).toBe(1);
});

test("Enter envía una vez y Shift+Enter no envía", async () => {
  const user = userEvent.setup();
  let calls = 0;
  const payload = questionPayload();
  authenticate();
  server.use(
    http.post("/v1/questions/stream", () => {
      calls += 1;
      return sseResponse(payload);
    }),
    http.get(`/v1/conversations/${CONVERSATION_ID}`, () =>
      HttpResponse.json(conversationDetail("hola", payload)),
    ),
  );
  renderApp("/");

  const field = await screen.findByLabelText("Pregunta");
  await user.click(field);
  await user.keyboard("hola{Shift>}{Enter}{/Shift}");
  expect(calls).toBe(0);
  await user.keyboard("{Enter}");
  expect(await screen.findByText(ANSWER)).toBeVisible();
  expect(calls).toBe(1);
});

test("anuncia el procesamiento y el timeout sin llamar al endpoint normal", async () => {
  const user = userEvent.setup();
  let jsonCalls = 0;
  setQuestionTimeoutForTests(300);
  authenticate();
  server.use(
    http.post("/v1/questions/stream", async () => {
      await delay("infinite");
      return sseResponse(questionPayload());
    }),
    http.post("/v1/questions", () => {
      jsonCalls += 1;
      return HttpResponse.json(questionPayload());
    }),
  );
  renderApp("/");

  await user.type(await screen.findByLabelText("Pregunta"), QUESTION);
  await user.click(screen.getByRole("button", { name: "Enviar" }));

  expect(await screen.findByText("Procesando la pregunta")).toBeVisible();
  expect(await screen.findByRole("alert")).toHaveTextContent("La pregunta tardó demasiado");
  expect(jsonCalls).toBe(0);
});

test("un error del servidor muestra el identificador y no el detalle interno", async () => {
  const user = userEvent.setup();
  let jsonCalls = 0;
  authenticate();
  server.use(
    http.post("/v1/questions/stream", () =>
      HttpResponse.json(
        { code: "internal_error", detail: "secret stack", request_id: "req-500" },
        { status: 500 },
      ),
    ),
    http.post("/v1/questions", () => {
      jsonCalls += 1;
      return HttpResponse.json(questionPayload());
    }),
  );
  renderApp("/");

  await user.type(await screen.findByLabelText("Pregunta"), QUESTION);
  await user.click(screen.getByRole("button", { name: "Enviar" }));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("Identificador: req-500");
  expect(alert).not.toHaveTextContent("secret stack");
  expect(jsonCalls).toBe(0);
});

test("un 429 indica la espera sugerida", async () => {
  const user = userEvent.setup();
  authenticate();
  server.use(
    http.post("/v1/questions/stream", () =>
      HttpResponse.json(
        { detail: "slow down" },
        { status: 429, headers: { "Retry-After": "12" } },
      ),
    ),
  );
  renderApp("/");

  await user.type(await screen.findByLabelText("Pregunta"), QUESTION);
  await user.click(screen.getByRole("button", { name: "Enviar" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("12 segundos");
});

test("si el stream no está disponible usa la pregunta normal", async () => {
  const user = userEvent.setup();
  let streamCalls = 0;
  let jsonCalls = 0;
  const payload = questionPayload();
  authenticate();
  server.use(
    http.post("/v1/questions/stream", () => {
      streamCalls += 1;
      return new HttpResponse(null, { status: 404 });
    }),
    http.post("/v1/questions", () => {
      jsonCalls += 1;
      return HttpResponse.json(payload);
    }),
    http.get(`/v1/conversations/${CONVERSATION_ID}`, () =>
      HttpResponse.json(conversationDetail(QUESTION, payload)),
    ),
  );
  renderApp("/");

  await user.type(await screen.findByLabelText("Pregunta"), QUESTION);
  await user.click(screen.getByRole("button", { name: "Enviar" }));

  expect(await screen.findByText(ANSWER)).toBeVisible();
  expect(streamCalls).toBe(1);
  expect(jsonCalls).toBe(1);
});

test("el selector envía el modelo elegido", async () => {
  const user = userEvent.setup();
  let sent: { llm?: string } | undefined;
  authenticate();
  server.use(
    http.get("/v1/models", () =>
      HttpResponse.json({
        items: [
          { id: "ollama:qwen2.5:7b", label: "qwen2.5:7b", provider: "ollama" },
          { id: "ollama:qwen3.5:2b", label: "qwen3.5:2b", provider: "ollama" },
          { id: "external", label: "modelo-externo", provider: "external" },
        ],
      }),
    ),
    http.post("/v1/questions/stream", async ({ request }) => {
      sent = (await request.json()) as { llm?: string };
      return sseResponse(questionPayload());
    }),
    http.get(`/v1/conversations/${CONVERSATION_ID}`, () =>
      HttpResponse.json(conversationDetail(QUESTION, questionPayload())),
    ),
  );
  renderApp("/");

  const model = await screen.findByLabelText("Modelo");
  expect(await screen.findByRole("option", { name: "API externa (modelo-externo)" })).toBeVisible();
  expect(screen.queryByText(/sk-|https:\/\//)).not.toBeInTheDocument();
  await user.selectOptions(model, "ollama:qwen3.5:2b");
  await user.type(screen.getByLabelText("Pregunta"), QUESTION);
  await user.click(screen.getByRole("button", { name: "Enviar" }));

  expect(await screen.findByText(ANSWER)).toBeVisible();
  expect(sent?.llm).toBe("ollama:qwen3.5:2b");
});

test("un 503 de generación muestra el detalle y el identificador", async () => {
  const user = userEvent.setup();
  authenticate();
  server.use(
    http.post("/v1/questions/stream", () => {
      const body = `event: error\ndata: ${JSON.stringify({
        request_id: "fb8307f9-7888-4187-b834-7e7e9b27f443",
        code: "llm_unavailable",
        detail: "El modelo no está disponible",
      })}\n\n`;
      return new HttpResponse(body, {
        status: 503,
        headers: { "Content-Type": "text/event-stream" },
      });
    }),
  );
  renderApp("/");

  await user.type(await screen.findByLabelText("Pregunta"), QUESTION);
  await user.click(screen.getByRole("button", { name: "Enviar" }));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("El modelo no está disponible");
  expect(alert).toHaveTextContent("fb8307f9-7888-4187-b834-7e7e9b27f443");
  expect(alert).not.toHaveTextContent("Comunica este identificador a soporte");
});

test("el feedback se confirma una sola vez y se puede sustituir", async () => {
  const user = userEvent.setup();
  const payload = questionPayload();
  let puts = 0;
  server.use(
    http.post("/v1/auth/refresh", () => HttpResponse.json(sessionBody(USER_A))),
    http.get("/v1/conversations", () => HttpResponse.json(conversationPage("Vacaciones"))),
    http.get(`/v1/conversations/${CONVERSATION_ID}`, () =>
      HttpResponse.json(conversationDetail(QUESTION, payload)),
    ),
    http.put(`/v1/messages/${MESSAGE_ID}/feedback`, async ({ request }) => {
      puts += 1;
      const body = (await request.json()) as { rating?: string };
      return HttpResponse.json({
        message_id: MESSAGE_ID,
        rating: body.rating,
        reason: null,
        updated_at: "2026-10-01T12:00:02Z",
      });
    }),
  );
  renderApp(`/conversations/${CONVERSATION_ID}`);

  await user.click(await screen.findByRole("button", { name: "Útil" }));
  expect(await screen.findByText("Valoración guardada")).toBeVisible();
  expect(screen.getAllByText("Valoración guardada")).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Útil" })).toHaveAttribute("aria-pressed", "true");

  await user.click(screen.getByRole("button", { name: "No útil" }));
  expect(screen.getAllByText("Valoración guardada")).toHaveLength(1);
  expect(screen.getByRole("button", { name: "No útil" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "Útil" })).toHaveAttribute("aria-pressed", "false");
  expect(puts).toBe(2);
});

test("al abrir la conversación el voto ya guardado queda marcado", async () => {
  const payload = questionPayload();
  const detail = conversationDetail(QUESTION, payload);
  const assistant = detail.messages[1] as { feedback_rating?: string };
  assistant.feedback_rating = "helpful";
  let puts = 0;
  server.use(
    http.post("/v1/auth/refresh", () => HttpResponse.json(sessionBody(USER_A))),
    http.get("/v1/conversations", () => HttpResponse.json(conversationPage("Vacaciones"))),
    http.get(`/v1/conversations/${CONVERSATION_ID}`, () => HttpResponse.json(detail)),
    http.put(`/v1/messages/${MESSAGE_ID}/feedback`, () => {
      puts += 1;
      return HttpResponse.json({
        message_id: MESSAGE_ID,
        rating: "helpful",
        reason: null,
        updated_at: "2026-10-01T12:00:02Z",
      });
    }),
  );
  renderApp(`/conversations/${CONVERSATION_ID}`);

  expect(await screen.findByRole("button", { name: "Útil" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(screen.getByRole("button", { name: "No útil" })).toHaveAttribute("aria-pressed", "false");
  expect(screen.getByText("Valoración guardada")).toBeVisible();
  expect(puts).toBe(0);
});

test("la conversación nueva queda en la lista antes de la respuesta", async () => {
  const user = userEvent.setup();
  const title = "¿Cuántos días de vacaciones hay?";
  let release = (): void => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  authenticate();
  server.use(
    http.get(`/v1/conversations/${CONVERSATION_ID}`, () =>
      HttpResponse.json(conversationDetail(title, questionPayload())),
    ),
    http.post("/v1/questions/stream", async () => {
      await gate;
      return sseResponse(questionPayload());
    }),
  );
  renderApp("/");

  await user.type(await screen.findByLabelText("Pregunta"), title);
  await user.click(screen.getByRole("button", { name: "Enviar" }));

  expect(await screen.findByRole("link", { name: title })).toBeVisible();
  expect(screen.getByText("Procesando la pregunta")).toBeVisible();
  release();
  expect(await screen.findByText(ANSWER)).toBeVisible();
});
