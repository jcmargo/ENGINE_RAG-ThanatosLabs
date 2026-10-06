import type { QueryClient } from "@tanstack/react-query";

export function forgetSecrets(queryClient: QueryClient): void {
  const cache = queryClient.getMutationCache();
  for (const mutation of [...cache.getAll()]) {
    if (holdsSecret(mutation.state.data)) {
      cache.remove(mutation);
    }
  }
}

function holdsSecret(data: unknown): boolean {
  if (typeof data !== "object" || data === null) {
    return false;
  }
  return "secret" in data || "activationToken" in data;
}
