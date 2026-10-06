import { expect, test } from "vitest";

import { MAX_UPLOAD_BYTES, uploadProblem } from "./limits.ts";
import { nextPollDelay, pollDelay, resetPollScaleForTests, setPollScaleForTests } from "./poll.ts";

test("acepta las extensiones del registro y el tope de 25 MiB", () => {
  expect(uploadProblem(new File(["a"], "nota.pdf"))).toBeNull();
  expect(uploadProblem(new File(["a"], "nota.PDF"))).toBeNull();
  expect(uploadProblem(new File(["a"], "nota.docx"))).toBeNull();
  expect(uploadProblem(new File(["a"], "nota.txt"))).toBeNull();
  expect(uploadProblem(new File(["a"], "nota.md"))).toBeNull();
  expect(uploadProblem(new File(["a"], "nota.markdown"))).toBeNull();
  expect(uploadProblem(new File(["a"], "nota.exe"))).toBe("El tipo de archivo no está permitido.");
  expect(uploadProblem(new File(["a"], "nota.doc"))).toBe("El tipo de archivo no está permitido.");

  const exact = new File(["a"], "nota.pdf");
  Object.defineProperty(exact, "size", { value: MAX_UPLOAD_BYTES });
  expect(uploadProblem(exact)).toBeNull();

  const over = new File(["a"], "nota.pdf");
  Object.defineProperty(over, "size", { value: MAX_UPLOAD_BYTES + 1 });
  expect(uploadProblem(over)).toBe("El archivo supera el máximo de 25 MiB.");
});

test("el sondeo empieza en 1 s, se duplica y se detiene en 8 s", () => {
  expect(pollDelay(0)).toBe(1000);
  expect(pollDelay(1)).toBe(2000);
  expect(pollDelay(2)).toBe(4000);
  expect(pollDelay(3)).toBe(8000);
  expect(pollDelay(6)).toBe(8000);

  const clock = { updatedAt: 0, attempt: 0 };
  expect(nextPollDelay(false, clock, 5)).toBe(false);
  expect(nextPollDelay(true, clock, 5)).toBe(1000);
  expect(nextPollDelay(true, clock, 5)).toBe(1000);
  expect(nextPollDelay(true, clock, 9)).toBe(2000);
  expect(nextPollDelay(false, clock, 9)).toBe(false);
  expect(clock.attempt).toBe(0);

  setPollScaleForTests(0.01);
  expect(pollDelay(0)).toBe(10);
  resetPollScaleForTests();
  expect(pollDelay(0)).toBe(1000);
});
