import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { TENANT, USER_A, sessionBody } from "../../test/fixtures.ts";
import { renderApp } from "../../test/render.tsx";
import { server } from "../../test/server.ts";

const TENANT_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc";
const OTHER_ID = "ffffffff-ffff-ffff-ffff-ffffffffffff";
const TOKEN = "token-activacion-proveedor-55";

function providerSession() {
  return { ...sessionBody(USER_A), principal_type: "provider_admin", tenant_id: null };
}

function tenantItem(overrides: Record<string, unknown> = {}) {
  return {
    id: TENANT_ID,
    name: "Norte",
    slug: "norte",
    status: "active",
    created_at: "2026-10-01T12:00:00Z",
    updated_at: "2026-10-01T12:00:00Z",
    ...overrides,
  };
}

function watchContent() {
  const hits: string[] = [];
  server.use(
    http.all(({ request }) => {
      const path = new URL(request.url).pathname;
      return (
        path.startsWith("/v1/documents") ||
        path.startsWith("/v1/questions") ||
        path.startsWith("/v1/conversations")
      );
    }, ({ request }) => {
      hits.push(new URL(request.url).pathname);
      return new HttpResponse(null, { status: 404 });
    }),
  );
  return hits;
}

test("el proveedor entra sin empresa y no pide contenido", async () => {
  const user = userEvent.setup();
  const hits = watchContent();
  let body: Record<string, unknown> = {};
  server.use(
    http.post("/v1/auth/login", async ({ request }) => {
      body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json(providerSession());
    }),
    http.get("/v1/provider/tenants", () => HttpResponse.json({ items: [], next_cursor: null, total: 0 })),
  );

  renderApp("/login");
  expect(await screen.findByRole("heading", { name: "Iniciar sesión" })).toBeVisible();
  await user.type(screen.getByLabelText("Correo"), "proveedor@thanatos.test");
  await user.type(screen.getByLabelText("Contraseña"), "secreta");
  await user.click(screen.getByRole("button", { name: "Entrar" }));

  expect(await screen.findByRole("heading", { name: "Empresas" })).toBeVisible();
  expect(body.tenant_slug).toBeUndefined();
  expect(body.email).toBe("proveedor@thanatos.test");
  expect(screen.queryByRole("link", { name: "Nueva conversación" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Documentos" })).not.toBeInTheDocument();
  expect(hits).toEqual([]);
});

test("crea y suspende empresas sin pedir contenido", async () => {
  const user = userEvent.setup();
  const hits = watchContent();
  let status = "active";
  const listed: Record<string, unknown>[] = [];
  server.use(
    http.post("/v1/auth/refresh", () => HttpResponse.json(providerSession())),
    http.get("/v1/provider/tenants", () =>
      HttpResponse.json({ items: listed.map((item) => ({ ...item, status })), next_cursor: null, total: listed.length }),
    ),
    http.post("/v1/provider/tenants", async ({ request }) => {
      const body = (await request.json()) as { name?: string; slug?: string; admin_email?: string };
      listed.push(tenantItem({ name: body.name, slug: body.slug }));
      return HttpResponse.json(
        {
          ...tenantItem({ name: body.name, slug: body.slug }),
          admin_user_id: USER_A,
          activation_token: TOKEN,
        },
        { status: 201 },
      );
    }),
    http.post(`/v1/provider/tenants/${TENANT_ID}/suspend`, () => {
      status = "suspended";
      return HttpResponse.json(tenantItem({ status }));
    }),
    http.post(`/v1/provider/tenants/${TENANT_ID}/reactivate`, () => {
      status = "active";
      return HttpResponse.json(tenantItem({ status }));
    }),
    http.get("/v1/provider/usage", () =>
      HttpResponse.json({
        documents: 7,
        bytes: 90,
        chunks: 8,
        questions: 5,
        abstentions: 1,
        errors: 0,
        p50_ms: 40,
        p95_ms: 90,
        tenant_id: TENANT,
      }),
    ),
  );

  const { client } = renderApp("/");
  expect(await screen.findByRole("heading", { name: "Empresas" })).toBeVisible();
  expect(screen.queryByRole("link", { name: "Nueva conversación" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Documentos" })).not.toBeInTheDocument();

  await user.type(screen.getByLabelText("Nombre"), "Norte");
  await user.type(screen.getByLabelText("Slug"), "norte");
  await user.type(screen.getByLabelText("Correo del administrador"), "admin@norte.test");
  await user.click(screen.getByRole("button", { name: "Crear empresa" }));

  expect(await screen.findByText(TOKEN)).toBeVisible();
  expect(JSON.stringify(client.getQueryCache().getAll().map((query) => query.state.data))).not.toContain(TOKEN);
  await user.click(screen.getByRole("checkbox", { name: "Ya lo guardé" }));
  await user.click(screen.getByRole("button", { name: "Cerrar" }));
  expect(screen.queryByText(TOKEN)).not.toBeInTheDocument();
  expect(JSON.stringify(client.getMutationCache().getAll().map((mutation) => mutation.state.data))).not.toContain(
    TOKEN,
  );
  expect(screen.getByText("Norte")).toBeVisible();

  await user.click(screen.getByRole("button", { name: "Suspender Norte" }));
  expect(screen.getByRole("heading", { name: "Suspender Norte" })).toBeVisible();
  expect(screen.getByText("Sus usuarios no podrán acceder en la siguiente solicitud.")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Confirmar suspensión" }));
  expect(await screen.findByRole("cell", { name: "Suspendida" })).toBeVisible();

  await user.click(screen.getByRole("button", { name: "Reactivar Norte" }));
  expect(screen.getByRole("heading", { name: "Reactivar Norte" })).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Confirmar reactivación" }));
  expect(await screen.findByRole("cell", { name: "Activa" })).toBeVisible();

  await user.click(screen.getByRole("link", { name: "Uso global" }));
  expect(await screen.findByRole("heading", { name: "Uso global" })).toBeVisible();
  const global = screen.getByRole("row", { name: /Global/ });
  expect(global).toHaveTextContent("7");
  expect(global).toHaveTextContent("90");
  expect(screen.queryByText(TENANT)).not.toBeInTheDocument();
  expect(hits).toEqual([]);
});

test("filtra el estado en la API y el nombre sobre lo ya cargado", async () => {
  const user = userEvent.setup();
  const hits = watchContent();
  const urls: string[] = [];
  server.use(
    http.post("/v1/auth/refresh", () => HttpResponse.json(providerSession())),
    http.get("/v1/provider/tenants", ({ request }) => {
      urls.push(request.url);
      const status = new URL(request.url).searchParams.get("status");
      const cursor = new URL(request.url).searchParams.get("cursor");
      const source = [
        tenantItem(),
        tenantItem({ id: OTHER_ID, name: "Sur", slug: "sur", status: "suspended" }),
      ];
      const items = source.filter((item) => status === null || item.status === status);
      return HttpResponse.json({
        items: cursor === null ? items : [],
        next_cursor: cursor === null ? "cursor-2" : null,
        total: items.length,
      });
    }),
  );

  renderApp("/provider/tenants");
  expect(await screen.findByText("Norte")).toBeVisible();
  expect(screen.getByText("Sur")).toBeVisible();
  const firstCount = urls.length;

  await user.type(screen.getByLabelText("Buscar"), "norte");
  expect(screen.getByText("Norte")).toBeVisible();
  expect(screen.queryByText("Sur")).not.toBeInTheDocument();
  expect(urls).toHaveLength(firstCount);
  expect(urls.some((url) => url.includes("norte"))).toBe(false);

  await user.clear(screen.getByLabelText("Buscar"));
  await user.selectOptions(screen.getByLabelText("Estado"), "suspended");
  await waitFor(() => expect(urls.some((url) => url.includes("status=suspended"))).toBe(true));
  expect(await screen.findByText("Sur")).toBeVisible();
  expect(screen.queryByText("Norte")).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Cargar más empresas" }));
  await waitFor(() => expect(urls.some((url) => url.includes("cursor=cursor-2"))).toBe(true));
  expect(hits).toEqual([]);
});

test("un proveedor en una ruta de empresa no monta el contenido", async () => {
  const hits = watchContent();
  server.use(
    http.post("/v1/auth/refresh", () => HttpResponse.json(providerSession())),
    http.get("/v1/provider/tenants", () => HttpResponse.json({ items: [], next_cursor: null, total: 0 })),
  );

  renderApp("/admin/documents");
  expect(await screen.findByRole("heading", { name: "Empresas" })).toBeVisible();
  expect(screen.queryByRole("heading", { name: "Documentos" })).not.toBeInTheDocument();
  expect(hits).toEqual([]);
});
