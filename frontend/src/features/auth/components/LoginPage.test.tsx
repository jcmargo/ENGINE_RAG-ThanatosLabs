import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";

import { server } from "../../../test/server.ts";
import {
  ACCESS_TOKEN,
  OTHER_TOKEN,
  USER_A,
  USER_B,
  conversationPage,
  emptyConversations,
  sessionBody,
} from "../../../test/fixtures.ts";
import { renderApp } from "../../../test/render.tsx";

test("muestra la marca y el formulario de acceso", async () => {
  renderApp("/login");

  expect(await screen.findByText("ThanatosLabs")).toBeVisible();
  expect(screen.getByText("Engine RAG")).toBeVisible();
  expect(screen.getByRole("heading", { level: 1, name: "Iniciar sesión" })).toBeVisible();
  expect(screen.getByLabelText("Empresa")).toBeVisible();
  expect(screen.getByLabelText("Correo")).toBeVisible();
  expect(screen.getByLabelText("Contraseña")).toBeVisible();
});

test("restaura la sesión con el refresh y no guarda el token", async () => {
  const logs: unknown[][] = [];
  const log = vi.spyOn(console, "log").mockImplementation((...args: unknown[]) => {
    logs.push(args);
  });
  const info = vi.spyOn(console, "info").mockImplementation((...args: unknown[]) => {
    logs.push(args);
  });
  const debug = vi.spyOn(console, "debug").mockImplementation((...args: unknown[]) => {
    logs.push(args);
  });
  const warn = vi.spyOn(console, "warn").mockImplementation((...args: unknown[]) => {
    logs.push(args);
  });
  const error = vi.spyOn(console, "error").mockImplementation((...args: unknown[]) => {
    logs.push(args);
  });
  const setItem = vi.spyOn(Storage.prototype, "setItem");
  server.use(
    http.post("/v1/auth/refresh", () => HttpResponse.json(sessionBody(USER_A))),
    http.get("/v1/conversations", () => HttpResponse.json(emptyConversations())),
  );

  renderApp("/");

  expect(await screen.findByRole("link", { name: "Nueva conversación" })).toBeVisible();
  expect(screen.queryByRole("heading", { name: "Iniciar sesión" })).not.toBeInTheDocument();
  expect(localStorage.length).toBe(0);
  expect(sessionStorage.length).toBe(0);
  expect(setItem).not.toHaveBeenCalled();
  expect(window.location.href).not.toContain(ACCESS_TOKEN);
  expect(document.cookie).not.toContain(ACCESS_TOKEN);
  expect(JSON.stringify(logs)).not.toContain(ACCESS_TOKEN);
  log.mockRestore();
  info.mockRestore();
  debug.mockRestore();
  warn.mockRestore();
  error.mockRestore();
  setItem.mockRestore();
});

test("inicia sesión con el teclado y aísla los datos al cerrar sesión", async () => {
  const user = userEvent.setup();
  let current = "ana";
  const loginCalls = { count: 0 };
  server.use(
    http.post("/v1/auth/login", async ({ request }) => {
      loginCalls.count += 1;
      const body = (await request.json()) as { email?: string };
      current = body.email?.includes("bea") ? "bea" : "ana";
      const token = current === "ana" ? ACCESS_TOKEN : OTHER_TOKEN;
      const userId = current === "ana" ? USER_A : USER_B;
      return HttpResponse.json(sessionBody(userId, token));
    }),
    http.post("/v1/auth/logout", () => new HttpResponse(null, { status: 204 })),
    http.get("/v1/conversations", () =>
      HttpResponse.json(
        conversationPage(current === "ana" ? "Secreto de Ana" : "Notas de Bea"),
      ),
    ),
  );
  renderApp("/login");

  expect(await screen.findByRole("heading", { name: "Iniciar sesión" })).toBeVisible();
  await user.type(screen.getByLabelText("Empresa"), "acme");
  await user.type(screen.getByLabelText("Correo"), "ana@acme.test");
  await user.type(screen.getByLabelText("Contraseña"), "secreta");
  await user.keyboard("{Enter}");

  expect(await screen.findByText("Secreto de Ana")).toBeVisible();
  expect(loginCalls.count).toBe(1);
  expect(localStorage.length).toBe(0);
  expect(sessionStorage.length).toBe(0);
  expect(window.location.href).not.toContain(ACCESS_TOKEN);

  await user.click(screen.getByRole("button", { name: "Cerrar sesión" }));

  expect(await screen.findByRole("heading", { name: "Iniciar sesión" })).toBeVisible();
  expect(screen.queryByText("Secreto de Ana")).not.toBeInTheDocument();
  expect(screen.queryByText(USER_A)).not.toBeInTheDocument();

  await user.type(screen.getByLabelText("Empresa"), "acme");
  await user.type(screen.getByLabelText("Correo"), "bea@acme.test");
  await user.type(screen.getByLabelText("Contraseña"), "otra");
  await user.click(screen.getByRole("button", { name: "Entrar" }));

  expect(await screen.findByText("Notas de Bea")).toBeVisible();
  expect(screen.queryByText("Secreto de Ana")).not.toBeInTheDocument();
  expect(window.location.href).not.toContain(OTHER_TOKEN);
});
