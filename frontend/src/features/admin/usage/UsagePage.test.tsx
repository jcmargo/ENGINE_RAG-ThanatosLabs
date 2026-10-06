import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { TENANT, USER_A, emptyConversations, sessionBody } from "../../../test/fixtures.ts";
import { renderApp } from "../../../test/render.tsx";
import { server } from "../../../test/server.ts";

function adminSession() {
  return { ...sessionBody(USER_A), principal_type: "tenant_admin" };
}

test("resume los días y el total sin texto ni usuarios", async () => {
  server.use(
    http.post("/v1/auth/refresh", () => HttpResponse.json(adminSession())),
    http.get("/v1/conversations", () => HttpResponse.json(emptyConversations())),
    http.get("/v1/usage/daily", () =>
      HttpResponse.json({
        items: [
          {
            day: "2026-10-01",
            documents: 2,
            bytes: 10,
            chunks: 3,
            questions: 4,
            abstentions: 1,
            errors: 0,
            p50_ms: 15,
            p95_ms: 40,
            tenant_id: TENANT,
            excerpt: "no debe verse",
          },
          {
            day: "2026-10-02",
            documents: 5,
            bytes: 7,
            chunks: 1,
            questions: 2,
            abstentions: 0,
            errors: 1,
            p50_ms: null,
            p95_ms: 80,
          },
        ],
      }),
    ),
  );

  renderApp("/admin/usage");
  expect(await screen.findByRole("heading", { name: "Uso" })).toBeVisible();
  expect(await screen.findByRole("row", { name: /2026-10-01/ })).toHaveTextContent("15");
  const total = screen.getByRole("row", { name: /Total/ });
  expect(total).toHaveTextContent("7");
  expect(total).toHaveTextContent("17");
  expect(total).toHaveTextContent("4");
  expect(total).toHaveTextContent("6");
  expect(total).toHaveTextContent("—");
  expect(screen.queryByText(TENANT)).not.toBeInTheDocument();
  expect(screen.queryByText("no debe verse")).not.toBeInTheDocument();
});
