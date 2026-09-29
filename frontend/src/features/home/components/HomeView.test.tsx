import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { HomeView } from "./HomeView.tsx";

test("muestra la marca ThanatosLabs y el producto Engine RAG", () => {
  render(<HomeView />);

  expect(screen.getByText("ThanatosLabs")).toBeVisible();
  expect(screen.getByRole("heading", { level: 1, name: "Engine RAG" })).toBeVisible();
  expect(screen.getByText("El frontend arrancó correctamente.")).toBeVisible();
});
