import { useSyncExternalStore } from "react";

type Entry = { pending: boolean; error: string | null };

const idle: Entry = { pending: false, error: null };
const entries = new Map<string, Entry>();
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) {
    listener();
  }
}

export function beginQuestion(conversationId: string): void {
  entries.set(conversationId, { pending: true, error: null });
  emit();
}

export function failQuestion(conversationId: string, message: string): void {
  entries.set(conversationId, { pending: false, error: message });
  emit();
}

export function finishQuestion(conversationId: string): void {
  entries.delete(conversationId);
  emit();
}

export function useQuestionPending(conversationId: string | undefined): Entry {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => readPending(conversationId),
    () => readPending(conversationId),
  );
}

function readPending(conversationId: string | undefined): Entry {
  if (conversationId === undefined) {
    return idle;
  }
  return entries.get(conversationId) ?? idle;
}
