import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  changeUserRole,
  deactivateUser,
  inviteUser,
  listUsers,
  type TenantUser,
} from "../../../api/admin.ts";
import { asApiError, type ApiError } from "../../../api/http.ts";
import type { Session } from "../../../api/session.ts";
import { useAuth } from "../../auth/auth-context.ts";
import { ApiAlert } from "../components/ApiAlert.tsx";
import { ConfirmDialog } from "../components/ConfirmDialog.tsx";
import { SecretDialog } from "../components/SecretDialog.tsx";
import { forgetSecrets } from "../secrets.ts";

function scopeOf(session: Session): string {
  return session.userId ?? session.tenantId ?? "session";
}

function usersKey(scope: string) {
  return ["session", scope, "users"] as const;
}

function roleLabel(role: string): string {
  if (role === "tenant_admin") return "Administrador";
  if (role === "member") return "Miembro";
  return role;
}

function statusLabel(status: string): string {
  if (status === "active") return "Activo";
  if (status === "disabled") return "Desactivado";
  return status;
}

export function UsersPage() {
  const { session } = useAuth();
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [token, setToken] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [confirming, setConfirming] = useState<TenantUser | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const scope = session === null ? "session" : scopeOf(session);

  const list = useInfiniteQuery({
    queryKey: usersKey(scope),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => listUsers(pageParam),
    getNextPageParam: (last) => (last.nextCursor === null ? undefined : last.nextCursor),
    enabled: session !== null,
  });
  const invite = useMutation({
    mutationFn: () => inviteUser(email.trim(), role),
    onSuccess: async (created) => {
      setToken(created.activationToken);
      setEmail("");
      setSuccess("Invitación creada.");
      await queryClient.invalidateQueries({ queryKey: usersKey(scope) });
    },
  });
  const change = useMutation({
    mutationFn: (input: { userId: string; nextRole: string }) => changeUserRole(input.userId, input.nextRole),
    onSuccess: async () => {
      setSuccess("Rol actualizado.");
      await queryClient.invalidateQueries({ queryKey: usersKey(scope) });
    },
  });
  const deactivate = useMutation({
    mutationFn: (userId: string) => deactivateUser(userId),
    onSuccess: async () => {
      setConfirming(null);
      setSuccess("Usuario desactivado.");
      await queryClient.invalidateQueries({ queryKey: usersKey(scope) });
    },
  });

  if (session === null) {
    return null;
  }

  const items = list.data?.pages.flatMap((page) => page.items) ?? [];
  const visibleError = error ?? asMutationError(invite.error) ?? asMutationError(change.error) ?? asMutationError(deactivate.error);

  function dismissToken() {
    setToken(null);
    invite.reset();
    forgetSecrets(queryClient);
  }

  return (
    <section className="panel">
      <h1>Usuarios</h1>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          setError(null);
          setSuccess(null);
          invite.mutate();
        }}
      >
        <label htmlFor="invite-email">Correo</label>
        <input
          id="invite-email"
          type="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
        <label htmlFor="invite-role">Rol</label>
        <select id="invite-role" value={role} onChange={(event) => setRole(event.target.value)}>
          <option value="member">Miembro</option>
          <option value="tenant_admin">Administrador</option>
        </select>
        <button type="submit" disabled={invite.isPending}>
          Invitar
        </button>
      </form>
      {visibleError ? <ApiAlert error={visibleError} /> : null}
      {success ? <p role="status">{success}</p> : null}
      {list.isPending ? <p role="status">Cargando usuarios</p> : null}
      {list.isError ? <ApiAlert error={asApiError(list.error)} /> : null}
      {list.isSuccess && items.length === 0 ? <p>Todavía no hay usuarios.</p> : null}
      {items.length > 0 ? (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Correo</th>
                <th>Rol</th>
                <th>Estado</th>
                <th>Acciones</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const draft = drafts[item.userId] ?? item.role;
                return (
                  <tr key={item.userId}>
                    <td>{item.email}</td>
                    <td>{roleLabel(item.role)}</td>
                    <td>{statusLabel(item.status)}</td>
                    <td>
                      <div className="actions">
                        <label>
                          Rol de {item.email}
                          <select
                            aria-label={`Rol de ${item.email}`}
                            value={draft}
                            onChange={(event) => {
                              setDrafts((current) => ({ ...current, [item.userId]: event.target.value }));
                            }}
                          >
                            <option value="member">Miembro</option>
                            <option value="tenant_admin">Administrador</option>
                          </select>
                        </label>
                        <button
                          type="button"
                          disabled={draft === item.role || change.isPending}
                          onClick={() => {
                            setError(null);
                            setSuccess(null);
                            change.mutate({ userId: item.userId, nextRole: draft });
                          }}
                        >
                          Guardar rol
                        </button>
                        {item.status === "active" ? (
                          <button
                            type="button"
                            onClick={() => {
                              setConfirming(item);
                            }}
                          >
                            Desactivar {item.email}
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      {list.hasNextPage ? (
        <button type="button" onClick={() => void list.fetchNextPage()} disabled={list.isFetchingNextPage}>
          Cargar más usuarios
        </button>
      ) : null}
      {token !== null ? (
        <SecretDialog
          title="Token de activación"
          secretLabel="Token"
          secret={token}
          onClose={dismissToken}
        />
      ) : null}
      {confirming ? (
        <ConfirmDialog
          title={`Desactivar ${confirming.email}`}
          effect="No podrá iniciar sesión en esta empresa."
          confirmLabel="Confirmar desactivación"
          busy={deactivate.isPending}
          onConfirm={() => {
            setError(null);
            setSuccess(null);
            deactivate.mutate(confirming.userId);
          }}
          onCancel={() => setConfirming(null)}
        />
      ) : null}
    </section>
  );
}

function asMutationError(error: unknown): ApiError | null {
  return error === null || error === undefined ? null : asApiError(error);
}
