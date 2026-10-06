import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";

import { uploadDocument } from "../../../api/admin.ts";
import { setSession } from "../../../api/session.ts";
import {
  ACCESS_TOKEN,
  TENANT,
  USER_A,
  emptyConversations,
  sessionBody,
} from "../../../test/fixtures.ts";
import { renderApp } from "../../../test/render.tsx";
import { server } from "../../../test/server.ts";
import { MAX_UPLOAD_BYTES } from "./limits.ts";
import { setPollScaleForTests } from "./poll.ts";

const DOC_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const VERSION_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";

function adminSession() {
  return { ...sessionBody(USER_A), principal_type: "tenant_admin" };
}

function memberSession() {
  return sessionBody(USER_A);
}

function documentItem(overrides: Record<string, unknown> = {}) {
  return {
    id: DOC_ID,
    display_name: "Politica de vacaciones.pdf",
    status: "ready",
    version: 2,
    byte_size: 2048,
    mime_type: "application/pdf",
    created_at: "2026-10-01T12:00:00Z",
    updated_at: "2026-10-01T12:00:01Z",
    public_error_code: null,
    chunk_count: 4,
    progress: 100,
    version_id: VERSION_ID,
    ...overrides,
  };
}

function documentPage(items: Record<string, unknown>[]) {
  return { items, next_cursor: null, total: items.length };
}

function authenticate(principal: "tenant_admin" | "member" = "tenant_admin") {
  server.use(
    http.post("/v1/auth/refresh", () =>
      HttpResponse.json(principal === "member" ? memberSession() : adminSession()),
    ),
    http.get("/v1/conversations", () => HttpResponse.json(emptyConversations())),
  );
}

test("lleva un archivo válido hasta listo y muestra sus metadatos", async () => {
  const user = userEvent.setup();
  const append = vi.spyOn(FormData.prototype, "append");
  let uploaded = false;
  let phase = "idle";
  let releaseUpload = () => undefined as void;
  const gate = new Promise<void>((resolve) => {
    releaseUpload = resolve;
  });
  authenticate();
  server.use(
    http.get("/v1/documents", () => HttpResponse.json(documentPage(uploaded ? [documentItem()] : []))),
    http.post("/v1/documents", async ({ request }) => {
      phase = "entered";
      await gate;
      const raw = await request.text();
      if (!raw.includes('name="file"') || !raw.includes("hola")) {
        return HttpResponse.json({ detail: "el archivo no llegó" }, { status: 400 });
      }
      uploaded = true;
      phase = "done";
      return HttpResponse.json(documentItem({ status: "queued", progress: 0 }), { status: 201 });
    }),
  );

  renderApp("/admin/documents");
  try {
    expect(await screen.findByRole("heading", { name: "Documentos" })).toBeVisible();
    void user.upload(
      screen.getByLabelText("Archivos"),
      new File(["hola"], "politica.pdf", { type: "application/pdf" }),
    );
    await waitFor(() => expect(phase).toBe("entered"));
    expect(screen.getByRole("progressbar", { name: "Progreso de la carga" })).toBeVisible();
    releaseUpload();
    expect(await screen.findByText("Documento cargado.")).toBeVisible();
  expect(screen.getByText("Politica de vacaciones.pdf")).toBeVisible();
  expect(screen.getByText("Listo")).toBeVisible();
  expect(screen.getByText("Versión 2")).toBeVisible();
  expect(screen.getByText("2048 bytes")).toBeVisible();
  expect(screen.getByText("4 fragmentos")).toBeVisible();
    expect(screen.getByText("application/pdf")).toBeVisible();
    expect(append).toHaveBeenCalledWith("file", expect.any(File), "politica.pdf");
  } finally {
    append.mockRestore();
  }
});

test("un archivo nuevo se suma a los que ya estaban", async () => {
  const user = userEvent.setup();
  const secondId = "cccccccc-cccc-cccc-cccc-cccccccccccc";
  let listed = [
    documentItem({
      id: DOC_ID,
      display_name: "Politica de vacaciones.md",
      version: 1,
      mime_type: "text/markdown",
    }),
  ];
  authenticate();
  server.use(
    http.get("/v1/documents", () => HttpResponse.json(documentPage(listed))),
    http.post("/v1/documents", () => {
      const created = documentItem({
        id: secondId,
        display_name: "algebra.pdf",
        version: 1,
        status: "queued",
        progress: 0,
        chunk_count: 0,
        version_id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
      });
      listed = [...listed, created];
      return HttpResponse.json(created, { status: 201 });
    }),
  );

  renderApp("/admin/documents");
  expect(await screen.findByText("Politica de vacaciones.md")).toBeVisible();
  await user.upload(
    screen.getByLabelText("Archivos"),
    new File(["pdf"], "algebra.pdf", { type: "application/pdf" }),
  );
  expect(await screen.findByText("algebra.pdf")).toBeVisible();
  expect(screen.getByText("Politica de vacaciones.md")).toBeVisible();
  expect(screen.getByText("Puedes elegir varios archivos a la vez. Cada uno se agrega a la lista.")).toBeVisible();
});

test("varios archivos elegidos juntos se agregan todos", async () => {
  const user = userEvent.setup();
  const posted: string[] = [];
  let listed: Record<string, unknown>[] = [];
  authenticate();
  server.use(
    http.get("/v1/documents", () => HttpResponse.json(documentPage(listed))),
    http.post("/v1/documents", async ({ request }) => {
      const raw = await request.text();
      const name = raw.includes("gastos") ? "gastos.md" : "soporte.md";
      posted.push(name);
      const created = documentItem({
        id: name === "gastos.md" ? "cccccccc-cccc-cccc-cccc-cccccccccccc" : "dddddddd-dddd-dddd-dddd-dddddddddddd",
        display_name: name,
        version: 1,
        status: "queued",
        progress: 0,
        chunk_count: 0,
        version_id:
          name === "gastos.md" ? "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee" : "ffffffff-ffff-ffff-ffff-ffffffffffff",
      });
      listed = [...listed, created];
      return HttpResponse.json(created, { status: 201 });
    }),
  );

  renderApp("/admin/documents");
  expect(await screen.findByRole("heading", { name: "Documentos" })).toBeVisible();
  const input = screen.getByLabelText("Archivos");
  expect(input).toHaveAttribute("multiple");
  await user.upload(input, [
    new File(["gastos"], "gastos.md", { type: "text/markdown" }),
    new File(["soporte"], "soporte.md", { type: "text/markdown" }),
  ]);
  expect(await screen.findByText("2 documentos cargados.")).toBeVisible();
  expect(screen.getByText("gastos.md")).toBeVisible();
  expect(screen.getByText("soporte.md")).toBeVisible();
  expect(posted).toEqual(["gastos.md", "soporte.md"]);
});

test("las acciones nombran el documento sin meter el nombre en el botón", async () => {
  authenticate();
  server.use(
    http.get("/v1/documents", () =>
      HttpResponse.json(
        documentPage([
          documentItem({
            display_name: "Algebra-Lineal-7ma-Edicion-Stanley-I-Grossman.pdf",
          }),
        ]),
      ),
    ),
  );

  renderApp("/admin/documents");
  const remove = await screen.findByRole("button", {
    name: "Eliminar Algebra-Lineal-7ma-Edicion-Stanley-I-Grossman.pdf",
  });
  expect(remove).toHaveTextContent("Eliminar");
  expect(remove).not.toHaveTextContent("Algebra");
  const replace = screen.getByLabelText(
    "Nueva versión de Algebra-Lineal-7ma-Edicion-Stanley-I-Grossman.pdf",
  );
  expect(replace).toHaveAccessibleName(
    "Nueva versión de Algebra-Lineal-7ma-Edicion-Stanley-I-Grossman.pdf",
  );
  expect(replace.closest("label")).toHaveTextContent("Nueva versión");
  expect(replace.closest("label")).not.toHaveTextContent("Algebra");
});

test("bloquea una extensión no permitida sin llamar al servidor", async () => {
  const posted = vi.fn();
  authenticate();
  server.use(
    http.get("/v1/documents", () => HttpResponse.json(documentPage([]))),
    http.post("/v1/documents", () => {
      posted();
      return HttpResponse.json(documentItem(), { status: 201 });
    }),
  );

  renderApp("/admin/documents");
  expect(await screen.findByRole("heading", { name: "Documentos" })).toBeVisible();
  fireEvent.change(screen.getByLabelText("Archivos"), {
    target: { files: [new File(["MZ"], "nota.exe")] },
  });
  expect(await screen.findByRole("alert")).toHaveTextContent("El tipo de archivo no está permitido.");
  expect(posted).not.toHaveBeenCalled();
});

test("bloquea un archivo de más de 25 MiB sin llamarlo al servidor", async () => {
  const user = userEvent.setup();
  const posted = vi.fn();
  authenticate();
  server.use(
    http.get("/v1/documents", () => HttpResponse.json(documentPage([]))),
    http.post("/v1/documents", () => {
      posted();
      return HttpResponse.json(documentItem(), { status: 201 });
    }),
  );
  const file = new File(["a"], "grande.pdf", { type: "application/pdf" });
  Object.defineProperty(file, "size", { value: MAX_UPLOAD_BYTES + 1 });

  renderApp("/admin/documents");
  expect(await screen.findByRole("heading", { name: "Documentos" })).toBeVisible();
  await user.upload(screen.getByLabelText("Archivos"), file);
  expect(await screen.findByRole("alert")).toHaveTextContent("El archivo supera el máximo de 25 MiB.");
  expect(posted).not.toHaveBeenCalled();
});

test("el servidor rechaza el archivo si se evade la interfaz", async () => {
  setSession({
    accessToken: ACCESS_TOKEN,
    expiresIn: 900,
    principalType: "tenant_admin",
    userId: USER_A,
    tenantId: TENANT,
  });
  server.use(
    http.post("/v1/documents", () =>
      HttpResponse.json({ detail: "El tipo de archivo no está permitido" }, { status: 400 }),
    ),
  );

  await expect(
    uploadDocument(new File(["MZ"], "nota.exe"), () => undefined),
  ).rejects.toMatchObject({
    status: 400,
    message: "El tipo de archivo no está permitido",
  });
});

test("deja de consultar cuando el documento queda estable", async () => {
  setPollScaleForTests(0.01);
  let calls = 0;
  authenticate();
  server.use(
    http.get("/v1/documents", () => {
      calls += 1;
      const status = calls === 1 ? "queued" : "ready";
      return HttpResponse.json(
        documentPage([documentItem({ status, progress: status === "ready" ? 100 : 10 })]),
      );
    }),
  );

  renderApp("/admin/documents");
  expect(await screen.findByText("Listo")).toBeVisible();
  const stable = calls;
  await new Promise((resolve) => setTimeout(resolve, 80));
  expect(calls).toBe(stable);
  expect(stable).toBeGreaterThan(1);
});

test("deja de consultar al salir de la página", async () => {
  const user = userEvent.setup();
  setPollScaleForTests(0.01);
  let calls = 0;
  authenticate();
  server.use(
    http.get("/v1/documents", () => {
      calls += 1;
      return HttpResponse.json(documentPage([documentItem({ status: "queued", progress: 0 })]));
    }),
  );

  renderApp("/admin/documents");
  expect(await screen.findByText(/En cola/)).toBeVisible();
  await waitFor(() => expect(calls).toBeGreaterThanOrEqual(2));
  await user.click(screen.getByRole("link", { name: "Nueva conversación" }));
  expect(await screen.findByRole("heading", { name: "Nueva conversación" })).toBeVisible();
  await new Promise((resolve) => setTimeout(resolve, 40));
  const stable = calls;
  await new Promise((resolve) => setTimeout(resolve, 80));
  expect(calls).toBe(stable);
});

test("cerrar la sesión de una empresa suspendida vuelve al acceso", async () => {
  const user = userEvent.setup();
  let refreshes = 0;
  server.use(
    http.post("/v1/auth/refresh", () => {
      refreshes += 1;
      if (refreshes > 1) {
        return HttpResponse.json({ detail: "Sesión no válida" }, { status: 401 });
      }
      return HttpResponse.json(adminSession());
    }),
    http.get("/v1/conversations", () => HttpResponse.json(emptyConversations())),
    http.get("/v1/documents", () =>
      HttpResponse.json(documentPage([documentItem({ display_name: "Contrato interno" })])),
    ),
    http.delete(`/v1/documents/${DOC_ID}`, () =>
      HttpResponse.json({ detail: "Sesión no válida" }, { status: 401 }),
    ),
  );

  const { client } = renderApp("/admin/documents");
  expect(await screen.findByText("Contrato interno")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Eliminar Contrato interno" }));
  await user.click(screen.getByRole("button", { name: "Confirmar eliminación" }));
  expect(await screen.findByRole("heading", { name: "Iniciar sesión" })).toBeVisible();
  expect(screen.queryByText("Contrato interno")).not.toBeInTheDocument();
  expect(JSON.stringify(client.getQueryCache().getAll().map((query) => query.state.data))).not.toContain(
    "Contrato interno",
  );
});

test("un cambio de principal vacía la caché y sale de administración", async () => {
  authenticate();
  server.use(
    http.get("/v1/documents", () =>
      HttpResponse.json(documentPage([documentItem({ display_name: "Nomina interna" })])),
    ),
  );

  const { client } = renderApp("/admin/documents");
  expect(await screen.findByText("Nomina interna")).toBeVisible();
  setSession({
    accessToken: ACCESS_TOKEN,
    expiresIn: 900,
    principalType: "member",
    userId: USER_A,
    tenantId: TENANT,
  });
  expect(await screen.findByRole("heading", { name: "Nueva conversación" })).toBeVisible();
  expect(screen.queryByRole("heading", { name: "Documentos" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Documentos" })).not.toBeInTheDocument();
  expect(JSON.stringify(client.getQueryCache().getAll().map((query) => query.state.data))).not.toContain(
    "Nomina interna",
  );
});

test("un miembro no abre administración ni consulta documentos", async () => {
  const docs = vi.fn();
  authenticate("member");
  server.use(
    http.get("/v1/documents", () => {
      docs();
      return HttpResponse.json(documentPage([documentItem()]));
    }),
  );

  renderApp("/admin/documents");
  expect(await screen.findByRole("link", { name: "Nueva conversación" })).toBeVisible();
  expect(screen.queryByRole("heading", { name: "Documentos" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Documentos" })).not.toBeInTheDocument();
  expect(docs).not.toHaveBeenCalled();
});

test("muestra 403 sin metadatos ajenos y 404 al reintentar", async () => {
  const user = userEvent.setup();
  authenticate();
  server.use(
    http.get("/v1/documents", () =>
      HttpResponse.json(
        { detail: "prohibido", display_name: "Secreto ajeno" },
        { status: 403 },
      ),
    ),
  );

  const forbidden = renderApp("/admin/documents");
  expect(await screen.findByRole("alert")).toHaveTextContent("No tienes permiso para esta acción.");
  expect(screen.queryByText("Secreto ajeno")).not.toBeInTheDocument();
  forbidden.unmount();

  server.use(
    http.get("/v1/documents", () =>
      HttpResponse.json(
        documentPage([
          documentItem({ status: "failed", progress: 0, public_error_code: "parse_error" }),
        ]),
      ),
    ),
    http.post(`/v1/documents/${DOC_ID}/versions/${VERSION_ID}/retry`, () =>
      HttpResponse.json({ detail: "Documento no encontrado" }, { status: 404 }),
    ),
  );
  renderApp("/admin/documents");
  expect(await screen.findByText("Falló")).toBeVisible();
  expect(screen.getByText("parse_error")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Reintentar Politica de vacaciones.pdf" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Documento no encontrado");
});

test("un error 500 muestra el identificador y oculta el detalle interno", async () => {
  authenticate();
  server.use(
    http.get("/v1/documents", () =>
      HttpResponse.json(
        { detail: "trace interna del worker", request_id: "req-500-admin" },
        { status: 500 },
      ),
    ),
  );

  renderApp("/admin/documents");
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("Identificador: req-500-admin");
  expect(alert).not.toHaveTextContent("trace interna");
});

test("eliminar nombra el documento y explica que pasa a eliminación", async () => {
  const user = userEvent.setup();
  let deleted = false;
  authenticate();
  server.use(
    http.get("/v1/documents", () =>
      HttpResponse.json(documentPage([documentItem({ status: deleted ? "deleted" : "ready" })])),
    ),
    http.delete(`/v1/documents/${DOC_ID}`, () => {
      deleted = true;
      return new HttpResponse(null, { status: 202 });
    }),
  );

  renderApp("/admin/documents");
  expect(await screen.findByText("Listo")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Eliminar Politica de vacaciones.pdf" }));
  expect(screen.getByRole("heading", { name: "Eliminar Politica de vacaciones.pdf" })).toBeVisible();
  expect(screen.getByText("El documento pasará a eliminación y dejará de usarse en las respuestas.")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Cancelar" }));
  expect(deleted).toBe(false);
  expect(screen.queryByRole("heading", { name: "Eliminar Politica de vacaciones.pdf" })).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Eliminar Politica de vacaciones.pdf" }));
  await user.click(screen.getByRole("button", { name: "Confirmar eliminación" }));
  expect(await screen.findByText("El documento pasó a eliminación.")).toBeVisible();
  expect(deleted).toBe(true);
});
