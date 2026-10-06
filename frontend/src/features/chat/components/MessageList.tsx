import type { ChatMessage } from "../../../api/http.ts";
import { CitationList } from "./CitationList.tsx";
import { FeedbackControls } from "./FeedbackControls.tsx";

export function MessageList({
  scope,
  conversationId,
  messages,
}: {
  scope: string;
  conversationId: string | undefined;
  messages: ChatMessage[];
}) {
  if (messages.length === 0) {
    return (
      <p>Escribe una pregunta. Se responderá solo con los documentos de tu empresa.</p>
    );
  }
  return (
    <ol className="messages">
      {messages.map((message) => (
        <li key={message.id}>
          <article>
            <h2>{message.role === "user" ? "Tú" : "Respuesta"}</h2>
            <p className="message-text">{message.content}</p>
            {message.role === "assistant" && message.abstained ? (
              <p role="status">Sin evidencia suficiente</p>
            ) : null}
            {message.role === "assistant" && !message.abstained ? (
              <CitationList citations={message.citations} />
            ) : null}
            {message.role === "assistant" ? (
              <FeedbackControls
                scope={scope}
                conversationId={conversationId}
                messageId={message.id}
                rating={message.feedbackRating}
              />
            ) : null}
          </article>
        </li>
      ))}
    </ol>
  );
}
