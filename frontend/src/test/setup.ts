import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, beforeEach } from "vitest";

import { resetPollScaleForTests } from "../features/admin/documents/poll.ts";
import { resetClientForTests } from "../api/http.ts";
import { server } from "./server.ts";

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
});

beforeEach(() => {
  resetClientForTests();
  resetPollScaleForTests();
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  server.resetHandlers();
});

afterAll(() => {
  server.close();
});
