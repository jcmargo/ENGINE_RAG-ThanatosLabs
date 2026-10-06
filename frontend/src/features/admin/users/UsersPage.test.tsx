import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { USER_A, emptyConversations, sessionBody } from "../../../test/fixtures.ts";
import { renderApp } from "../../../test/render.tsx";
import { server } from "../../../test/server.ts";

const USER_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd";
const TOKEN = "token-activacion-una-vez-77";
const LAST_ADMIN = "No se puede degradar ni desactivar al último administrador activo";

function adminSession() {
  return { ...sessionBody(USER_A), principal_type: "tenant_admin" };
}

function userItem(overrides: Record<string, unknown> = {}) {
  return {
    user_id: USER_ID,
    email: "ana@acme.test",
    role: "member",
    status: "active",
    created_at: "2026-10-01T12:00:00Z",
    ...overrides,
  };
}

function page(items: Record<string, unknown>[]) {
  return { items, next_cursor: null, total: items.length };
}

test("el token de activación se muestra una vez y no entra en la caché", async () => {
  const user = userEvent.setup();
  let invited = false;
  let role = "member";
  server.use(
    http.post("/v1/auth/refresh", () => HttpResponse.json(adminSession())),
    http.get("/v1/conversations", () => HttpResponse.json(emptyConversations())),
    http.get("/v1/users", () => HttpResponse.json(page(invited ? [userItem({ role })] : []))),
    http.post("/v1/users", async ({ request }) => {
      const body = (await request.json()) as { email?: string; role?: string };
      invited = true;
      role = body.role ?? "member";
      return HttpResponse.json(
        {
          user_id: USER_ID,
          email: body.email,
          role,
          activation_token: TOKEN,
        },
        { status: 201 },
      );
    }),
    http.patch("/v1/users/:userId", async ({ request }) => {
      const body = (await request.json()) as { role?: string };
      role = body.role ?? role;
      return HttpResponse.json(userItem({ role }));
    }),
  );

  const { client } = renderApp("/admin/users");
  expect(await screen.findByRole("heading", { name: "Usuarios" })).toBeVisible();
  await user.type(screen.getByLabelText("Correo"), "ana@acme.test");
  await user.selectOptions(screen.getByLabelText("Rol"), "tenant_admin");
  await user.click(screen.getByRole("button", { name: "Invitar" }));

  expect(await screen.findByText(TOKEN)).toBeVisible();
  expect(screen.getByRole("button", { name: "Cerrar" })).toBeDisabled();
  expect(JSON.stringify(client.getQueryCache().getAll().map((query) => query.state.data))).not.toContain(TOKEN);
  expect(localStorage.length).toBe(0);
  expect(sessionStorage.length).toBe(0);

  await user.click(screen.getByRole("checkbox", { name: "Ya lo guardé" }));
  await user.click(screen.getByRole("button", { name: "Cerrar" }));
  expect(screen.queryByText(TOKEN)).not.toBeInTheDocument();
  expect(JSON.stringify(client.getQueryCache().getAll().map((query) => query.state.data))).not.toContain(TOKEN);
  expect(JSON.stringify(client.getMutationCache().getAll().map((mutation) => mutation.state.data))).not.toContain(
    TOKEN,
  );
  expect(screen.getByText("ana@acme.test")).toBeVisible();

  await user.selectOptions(screen.getByLabelText("Rol de ana@acme.test"), "member");
  await user.click(screen.getByRole("button", { name: "Guardar rol" }));
  expect(await screen.findByText("Rol actualizado.")).toBeVisible();
  expect(screen.getByRole("cell", { name: "Miembro" })).toBeVisible();
});

test("no degrada al último administrador activo", async () => {
  const user = userEvent.setup();
  server.use(
    http.post("/v1/auth/refresh", () => HttpResponse.json(adminSession())),
    http.get("/v1/conversations", () => HttpResponse.json(emptyConversations())),
    http.get("/v1/users", () =>
      HttpResponse.json(page([userItem({ role: "tenant_admin", email: "admin@acme.test" })])),
    ),
    http.post("/v1/users/:userId/deactivate", () =>
      HttpResponse.json({ detail: LAST_ADMIN }, { status: 409 }),
    ),
  );

  renderApp("/admin/users");
  expect(await screen.findByText("admin@acme.test")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Desactivar admin@acme.test" }));
  expect(screen.getByRole("heading", { name: "Desactivar admin@acme.test" })).toBeVisible();
  expect(screen.getByText("No podrá iniciar sesión en esta empresa.")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Confirmar desactivación" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(LAST_ADMIN);
  expect(screen.getByText("admin@acme.test")).toBeVisible();
  expect(screen.getByRole("cell", { name: "Administrador" })).toBeVisible();
  expect(screen.getByRole("cell", { name: "Activo" })).toBeVisible();
});
