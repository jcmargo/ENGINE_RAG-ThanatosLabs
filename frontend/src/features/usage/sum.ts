import type { UsageTotals } from "../../api/admin.ts";

const EMPTY: UsageTotals = {
  documents: 0,
  bytes: 0,
  chunks: 0,
  questions: 0,
  abstentions: 0,
  errors: 0,
  p50Ms: null,
  p95Ms: null,
};

export function sumUsage(items: UsageTotals[]): UsageTotals {
  return items.reduce(
    (total, item) => ({
      documents: total.documents + item.documents,
      bytes: total.bytes + item.bytes,
      chunks: total.chunks + item.chunks,
      questions: total.questions + item.questions,
      abstentions: total.abstentions + item.abstentions,
      errors: total.errors + item.errors,
      p50Ms: null,
      p95Ms: null,
    }),
    EMPTY,
  );
}
