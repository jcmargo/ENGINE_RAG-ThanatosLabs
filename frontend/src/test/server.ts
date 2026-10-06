import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";

export const server = setupServer(
  http.post("/v1/auth/refresh", () =>
    HttpResponse.json({ detail: "No autenticado" }, { status: 401 }),
  ),
  http.get("/v1/models", () =>
    HttpResponse.json({
      items: [{ id: "ollama:qwen2.5:7b", label: "qwen2.5:7b", provider: "ollama" }],
    }),
  ),
);
