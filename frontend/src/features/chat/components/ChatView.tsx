import { useParams } from "react-router";

import { asApiError } from "../../../api/http.ts";
import type { Session } from "../../../api/session.ts";
import { useAuth } from "../../auth/auth-context.ts";
import { sessionScope, useConversation } from "../hooks/useConversations.ts";
import { useQuestionPending } from "../inflight.ts";
import { Composer } from "./Composer.tsx";
import { MessageList } from "./MessageList.tsx";

export function ChatView() {
  const { conversationId } = useParams();
  const { session } = useAuth();
  if (session === null) {
    return null;
  }
  return <ChatPanel session={session} conversationId={conversationId} />;
}

function ChatPanel({
  session,
  conversationId,
}: {
  session: Session;
  conversationId: string | undefined;
}) {
  const detail = useConversation(session, conversationId);
  const pending = useQuestionPending(conversationId);
  const messages = detail.data?.messages ?? [];
  const waiting = pending.pending && messages.length === 0;

  return (
    <section className="chat" aria-label="Chat">
      <h1>{detail.data?.title ?? "Nueva conversación"}</h1>
      <p className="independence">
        Un seguimiento usa tu pregunta anterior solo para buscar. La respuesta sale de los documentos.
      </p>
      {conversationId !== undefined && detail.isPending ? (
        <p role="status">Cargando conversación</p>
      ) : null}
      {detail.isError ? <p role="alert">{asApiError(detail.error).message}</p> : null}
      {pending.error !== null ? <p role="alert">{pending.error}</p> : null}
      {waiting ? null : (
        <MessageList
          scope={sessionScope(session)}
          conversationId={conversationId}
          messages={messages}
        />
      )}
      <Composer session={session} conversationId={conversationId} />
    </section>
  );
}
