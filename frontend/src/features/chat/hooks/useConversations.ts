import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { getConversation, listConversations } from "../../../api/http.ts";
import type { Session } from "../../../api/session.ts";

export function sessionScope(session: Session): string {
  return session.userId ?? session.tenantId ?? "session";
}

export function conversationListKey(scope: string) {
  return ["session", scope, "conversations"] as const;
}

export function conversationKey(scope: string, conversationId: string) {
  return ["session", scope, "conversation", conversationId] as const;
}

export function useConversationList(session: Session) {
  const scope = sessionScope(session);
  return useInfiniteQuery({
    queryKey: conversationListKey(scope),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => listConversations(pageParam),
    getNextPageParam: (last) => last.nextCursor,
  });
}

export function useConversation(session: Session, conversationId: string | undefined) {
  const scope = sessionScope(session);
  return useQuery({
    queryKey: conversationKey(scope, conversationId ?? "draft"),
    queryFn: () => {
      if (conversationId === undefined) {
        throw new Error("La conversación todavía no existe");
      }
      return getConversation(conversationId);
    },
    enabled: conversationId !== undefined,
  });
}
