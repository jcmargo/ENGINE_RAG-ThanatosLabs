import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  asApiError,
  submitFeedback,
  type ConversationDetail,
  type FeedbackRating,
} from "../../../api/http.ts";
import { conversationKey } from "../hooks/useConversations.ts";

export function FeedbackControls({
  scope,
  conversationId,
  messageId,
  rating,
}: {
  scope: string;
  conversationId: string | undefined;
  messageId: string;
  rating: FeedbackRating | null;
}) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (next: FeedbackRating) => submitFeedback(messageId, next),
    onSuccess: (result) => {
      if (conversationId === undefined) {
        return;
      }
      queryClient.setQueryData<ConversationDetail>(
        conversationKey(scope, conversationId),
        (current) => {
          if (current === undefined) {
            return current;
          }
          return {
            ...current,
            messages: current.messages.map((item) =>
              item.id === messageId ? { ...item, feedbackRating: result.rating } : item,
            ),
          };
        },
      );
    },
  });
  const error = mutation.isError ? asApiError(mutation.error) : null;

  return (
    <div className="feedback">
      <p id={`feedback-${messageId}`}>¿Esta respuesta fue útil?</p>
      <button
        type="button"
        aria-pressed={rating === "helpful"}
        aria-describedby={`feedback-${messageId}`}
        disabled={mutation.isPending}
        onClick={() => {
          mutation.mutate("helpful");
        }}
      >
        Útil
      </button>
      <button
        type="button"
        aria-pressed={rating === "not_helpful"}
        aria-describedby={`feedback-${messageId}`}
        disabled={mutation.isPending}
        onClick={() => {
          mutation.mutate("not_helpful");
        }}
      >
        No útil
      </button>
      {rating !== null ? <p role="status">Valoración guardada</p> : null}
      {error ? (
        <p role="alert">
          {error.message}
          {error.status >= 500 ? ` Identificador: ${error.requestId}` : null}
        </p>
      ) : null}
    </div>
  );
}
