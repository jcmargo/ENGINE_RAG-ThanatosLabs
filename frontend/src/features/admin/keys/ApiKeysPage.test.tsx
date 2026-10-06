import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { USER_A, emptyConversations, sessionBody } from "../../../test/fixtures.ts";
import { renderApp } from "../../../test/render.tsx";
import { server } from "../../../test/server.ts";

const KEY_ID = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee";
const PREFIX = "rk_live_ab";
const SECRET = "secreto-de-un-solo-uso-9f3a";

function adminSession() {
  return { ...sessionBody(USER_A), principal_type: "tenant_admin" };
}

function keyItem(overrides: Record<string, unknown> = {}) {
  return {
    id: KEY_ID,
    prefix: PREFIX,
    scopes: ["query"],
    status: "active",
    created_at: "2026-10-01T12:00:00Z",
    updated_at: "2026-10-01T12:00:00Z",
    revoked_at: null,
    ...overrides,
  };
}

test("el secreto se muestra una vez y después solo queda el prefijo", async () => {
  const user = userEvent.setup();
  let created = false;
  let revoked = false;
  let scopes = ["query"];
  server.use(
    http.post("/v1/auth/refresh", () => HttpResponse.json(adminSession())),
    http.get("/v1/conversations", () => HttpResponse.json(emptyConversations())),
    http.get("/v1/api-keys", () =>
      HttpResponse.json({
        items: created
          ? [keyItem({ scopes, ...(revoked ? { status: "revoked", revoked_at: "2026-10-01T13:00:00Z" } : {}) })]
          : [],
      }),
    ),
    http.post("/v1/api-keys", async ({ request }) => {
      const body = (await request.json()) as { scopes?: string[] };
      expect(body.scopes).toEqual(["query", "documents:read"]);
      scopes = body.scopes ?? ["query"];
      created = true;
      return HttpResponse.json(
        {
          id: KEY_ID,
          prefix: PREFIX,
          scopes: body.scopes,
          status: "active",
          created_at: "2026-10-01T12:00:00Z",
          secret: SECRET,
        },
        { status: 201 },
      );
    }),
    http.post(`/v1/api-keys/${KEY_ID}/revoke`, () => {
      revoked = true;
      return HttpResponse.json({
        id: KEY_ID,
        prefix: PREFIX,
        status: "revoked",
        revoked_at: "2026-10-01T13:00:00Z",
      });
    }),
  );

  const { client } = renderApp("/admin/api-keys");
  expect(await screen.findByRole("heading", { name: "Claves API" })).toBeVisible();
  await user.click(screen.getByRole("checkbox", { name: "Leer documentos (documents:read)" }));
  await user.click(screen.getByRole("button", { name: "Crear clave" }));

  expect(await screen.findByText(SECRET)).toBeVisible();
  expect(screen.getByRole("button", { name: "Cerrar" })).toBeDisabled();
  expect(JSON.stringify(client.getQueryCache().getAll().map((query) => query.state.data))).not.toContain(SECRET);

  await user.click(screen.getByRole("checkbox", { name: "Ya lo guardé" }));
  await user.click(screen.getByRole("button", { name: "Cerrar" }));
  expect(screen.queryByText(SECRET)).not.toBeInTheDocument();
  expect(screen.getByText(PREFIX)).toBeVisible();
  expect(screen.getByText("query, documents:read")).toBeVisible();
  expect(JSON.stringify(client.getQueryCache().getAll().map((query) => query.state.data))).not.toContain(SECRET);
  expect(JSON.stringify(client.getMutationCache().getAll().map((mutation) => mutation.state.data))).not.toContain(
    SECRET,
  );
  expect(localStorage.length).toBe(0);

  await user.click(screen.getByRole("button", { name: `Revocar ${PREFIX}` }));
  expect(screen.getByRole("heading", { name: `Revocar ${PREFIX}` })).toBeVisible();
  expect(screen.getByText("La clave dejará de autorizar solicitudes.")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Confirmar revocación" }));
  expect(await screen.findByText("Revocada")).toBeVisible();
  expect(screen.queryByRole("button", { name: `Revocar ${PREFIX}` })).not.toBeInTheDocument();
  expect(screen.queryByText(SECRET)).not.toBeInTheDocument();
});
