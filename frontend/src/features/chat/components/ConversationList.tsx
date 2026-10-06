import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { NavLink, useNavigate } from "react-router";

import { asApiError, deleteConversation, renameConversation } from "../../../api/http.ts";
import type { Session } from "../../../api/session.ts";
import {
  conversationKey,
  conversationListKey,
  sessionScope,
  useConversationList,
} from "../hooks/useConversations.ts";

export function ConversationList({ session }: { session: Session }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const scope = sessionScope(session);
  const list = useConversationList(session);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const items = list.data?.pages.flatMap((page) => page.items) ?? [];

  async function saveTitle(event: FormEvent<HTMLFormElement>, conversationId: string) {
    event.preventDefault();
    setError(null);
    try {
      await renameConversation(conversationId, title.trim());
      setRenamingId(null);
      await queryClient.invalidateQueries({ queryKey: conversationListKey(scope) });
      await queryClient.invalidateQueries({ queryKey: conversationKey(scope, conversationId) });
    } catch (caught) {
      setError(asApiError(caught).message);
    }
  }

  async function confirmDelete(conversationId: string) {
    setError(null);
    try {
      await deleteConversation(conversationId);
      queryClient.removeQueries({ queryKey: conversationKey(scope, conversationId) });
      setConfirmId(null);
      await queryClient.invalidateQueries({ queryKey: conversationListKey(scope) });
      void navigate("/");
    } catch (caught) {
      setError(asApiError(caught).message);
    }
  }

  return (
    <nav aria-label="Conversaciones">
      <NavLink to="/" end>
        Nueva conversación
      </NavLink>
      {list.isPending ? <p role="status">Cargando conversaciones</p> : null}
      {list.isError ? <p role="alert">{asApiError(list.error).message}</p> : null}
      {list.isSuccess && items.length === 0 ? <p>Todavía no hay conversaciones.</p> : null}
      <ul>
        {items.map((item) => (
          <li key={item.id}>
            <NavLink to={`/conversations/${item.id}`}>{item.title}</NavLink>
            {renamingId === item.id ? (
              <form onSubmit={(event) => void saveTitle(event, item.id)}>
                <label htmlFor={`title-${item.id}`}>Título</label>
                <input
                  id={`title-${item.id}`}
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  required
                  maxLength={120}
                />
                <button type="submit">Guardar título</button>
                <button
                  type="button"
                  onClick={() => {
                    setRenamingId(null);
                  }}
                >
                  Cancelar
                </button>
              </form>
            ) : (
              <button
                type="button"
                onClick={() => {
                  setRenamingId(item.id);
                  setTitle(item.title);
                  setConfirmId(null);
                }}
              >
                Renombrar {item.title}
              </button>
            )}
            {confirmId === item.id ? (
              <>
                <button type="button" onClick={() => void confirmDelete(item.id)}>
                  Confirmar eliminación
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setConfirmId(null);
                  }}
                >
                  Cancelar
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={() => {
                  setConfirmId(item.id);
                  setRenamingId(null);
                }}
              >
                Eliminar {item.title}
              </button>
            )}
          </li>
        ))}
      </ul>
      {list.hasNextPage ? (
        <button type="button" onClick={() => void list.fetchNextPage()} disabled={list.isFetchingNextPage}>
          Cargar más conversaciones
        </button>
      ) : null}
      {error ? <p role="alert">{error}</p> : null}
    </nav>
  );
}
