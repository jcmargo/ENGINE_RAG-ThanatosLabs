import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";

import { server } from "../../../test/server.ts";
import { USER_A, emptyConversations, sessionBody } from "../../../test/fixtures.ts";
import { renderApp } from "../../../test/render.tsx";
import { ACTIVATION_READY_NOTICE } from "./LoginPage.tsx";

const TOKEN = "token-activacion-una-vez-77";
const PASSWORD = "contraseña-elegida";

function silenceConsole() {
  const logs: unknown[][] = [];
  const spies = (["log", "info", "debug", "warn", "error"] as const).map((method) =>
    vi.spyOn(console, method).mockImplementation((...args: unknown[]) => {
      logs.push(args);
    }),
  );
  return {
    logs,
    restore() {
      for (const spy of spies) {
        spy.mockRestore();
      }
    },
  };
}

async function reachPasswordStep(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("link", { name: "Activar cuenta" }));
  expect(await screen.findByRole("heading", { name: "Activar cuenta" })).toBeVisible();
  await user.type(screen.getByLabelText("Token de activación"), TOKEN);
  await user.click(screen.getByRole("button", { name: "Continuar" }));
  expect(await screen.findByRole("heading", { name: "Crear contraseña" })).toBeVisible();
}

test("desde el login se llega al token y después a crear contraseña", async () => {
  const user = userEvent.setup();
  renderApp("/login");

  expect(await screen.findByRole("heading", { name: "Iniciar sesión" })).toBeVisible();
  await user.click(screen.getByRole("link", { name: "Activar cuenta" }));
  expect(await screen.findByRole("heading", { name: "Activar cuenta" })).toBeVisible();
  expect(screen.getByLabelText("Token de activación")).toBeVisible();
  expect(window.location.href).not.toContain("token");

  await user.type(screen.getByLabelText("Token de activación"), TOKEN);
  await user.click(screen.getByRole("button", { name: "Continuar" }));

  expect(await screen.findByRole("heading", { name: "Crear contraseña" })).toBeVisible();
  expect(screen.getByLabelText("Contraseña")).toBeVisible();
  expect(screen.getByLabelText("Confirmar contraseña")).toBeVisible();
  expect(screen.queryByDisplayValue(TOKEN)).not.toBeInTheDocument();
  expect(window.location.href).not.toContain(TOKEN);
});

test("una confirmación distinta o un campo vacío no llama a la API", async () => {
  const user = userEvent.setup();
  let calls = 0;
  server.use(
    http.post("/v1/auth/activate", () => {
      calls += 1;
      return new HttpResponse(null, { status: 204 });
    }),
  );
  renderApp("/login");
  await reachPasswordStep(user);

  await user.type(screen.getByLabelText("Contraseña"), PASSWORD);
  await user.type(screen.getByLabelText("Confirmar contraseña"), "otra-distinta");
  await user.click(screen.getByRole("button", { name: "Crear contraseña" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Las contraseñas no coinciden.");
  expect(calls).toBe(0);
  expect(screen.getByRole("heading", { name: "Crear contraseña" })).toBeVisible();

  await user.clear(screen.getByLabelText("Confirmar contraseña"));
  const form = screen.getByRole("button", { name: "Crear contraseña" }).closest("form");
  if (form === null) {
    throw new Error("falta el formulario de contraseña");
  }
  fireEvent.submit(form);

  expect(await screen.findByRole("alert")).toHaveTextContent("Escribe la contraseña y confírmala.");
  expect(calls).toBe(0);
});

test("un 204 vuelve al login con el aviso y sin abrir sesión", async () => {
  const user = userEvent.setup();
  const consoleSpy = silenceConsole();
  const setItem = vi.spyOn(Storage.prototype, "setItem");
  let sent: unknown = null;
  server.use(
    http.post("/v1/auth/activate", async ({ request }) => {
      sent = await request.json();
      return new HttpResponse(null, { status: 204 });
    }),
  );
  const { client } = renderApp("/login");
  await reachPasswordStep(user);
  await user.type(screen.getByLabelText("Contraseña"), PASSWORD);
  await user.type(screen.getByLabelText("Confirmar contraseña"), PASSWORD);
  await user.click(screen.getByRole("button", { name: "Crear contraseña" }));

  expect(await screen.findByRole("heading", { name: "Iniciar sesión" })).toBeVisible();
  expect(screen.getByRole("status")).toHaveTextContent(ACTIVATION_READY_NOTICE);
  expect(screen.queryByRole("heading", { name: "Crear contraseña" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Nueva conversación" })).not.toBeInTheDocument();
  expect(sent).toEqual({ token: TOKEN, password: PASSWORD });
  expect(localStorage.length).toBe(0);
  expect(sessionStorage.length).toBe(0);
  expect(setItem).not.toHaveBeenCalled();
  expect(window.location.href).not.toContain(TOKEN);
  expect(document.cookie).not.toContain(TOKEN);
  expect(JSON.stringify(consoleSpy.logs)).not.toContain(TOKEN);
  expect(JSON.stringify(client.getQueryCache().getAll())).not.toContain(TOKEN);
  expect(document.body.textContent).not.toContain(TOKEN);
  consoleSpy.restore();
  setItem.mockRestore();
});

test("un 401 muestra el mensaje genérico del servidor", async () => {
  const user = userEvent.setup();
  server.use(
    http.post("/v1/auth/activate", () =>
      HttpResponse.json({ detail: "Token de activación inválido" }, { status: 401 }),
    ),
  );
  renderApp("/login");
  await reachPasswordStep(user);
  await user.type(screen.getByLabelText("Contraseña"), PASSWORD);
  await user.type(screen.getByLabelText("Confirmar contraseña"), PASSWORD);
  await user.click(screen.getByRole("button", { name: "Crear contraseña" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Token de activación inválido");
  expect(screen.getByRole("heading", { name: "Crear contraseña" })).toBeVisible();
  expect(screen.queryByRole("heading", { name: "Iniciar sesión" })).not.toBeInTheDocument();
});

test("un usuario autenticado no ve el formulario de activación", async () => {
  server.use(
    http.post("/v1/auth/refresh", () => HttpResponse.json(sessionBody(USER_A))),
    http.get("/v1/conversations", () => HttpResponse.json(emptyConversations())),
  );
  renderApp("/activate");

  expect(await screen.findByRole("link", { name: "Nueva conversación" })).toBeVisible();
  expect(screen.queryByRole("heading", { name: "Activar cuenta" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Crear contraseña" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Token de activación")).not.toBeInTheDocument();
});
