import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { createApiKey, listApiKeys, revokeApiKey, type ApiKeyItem } from "../../../api/admin.ts";
import { asApiError, type ApiError } from "../../../api/http.ts";
import type { Session } from "../../../api/session.ts";
import { useAuth } from "../../auth/auth-context.ts";
import { ApiAlert } from "../components/ApiAlert.tsx";
import { ConfirmDialog } from "../components/ConfirmDialog.tsx";
import { SecretDialog } from "../components/SecretDialog.tsx";
import { forgetSecrets } from "../secrets.ts";

const SCOPES = [
  { id: "query", label: "Preguntas (query)" },
  { id: "documents:read", label: "Leer documentos (documents:read)" },
  { id: "documents:write", label: "Escribir documentos (documents:write)" },
] as const;

function scopeOf(session: Session): string {
  return session.userId ?? session.tenantId ?? "session";
}

function keysKey(scope: string) {
  return ["session", scope, "api-keys"] as const;
}

function statusLabel(status: string): string {
  if (status === "active") return "Activa";
  if (status === "revoked") return "Revocada";
  return status;
}

export function ApiKeysPage() {
  const { session } = useAuth();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Record<string, boolean>>({ query: true });
  const [clientError, setClientError] = useState<string | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<ApiKeyItem | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const scope = session === null ? "session" : scopeOf(session);

  const list = useQuery({
    queryKey: keysKey(scope),
    queryFn: () => listApiKeys(),
    enabled: session !== null,
  });
  const create = useMutation({
    mutationFn: (scopes: string[]) => createApiKey(scopes),
    onSuccess: async (created) => {
      setSecret(created.secret);
      setSuccess("Clave creada.");
      await queryClient.invalidateQueries({ queryKey: keysKey(scope) });
    },
  });
  const revoke = useMutation({
    mutationFn: (apiKeyId: string) => revokeApiKey(apiKeyId),
    onSuccess: async () => {
      setConfirming(null);
      setSuccess("Clave revocada.");
      await queryClient.invalidateQueries({ queryKey: keysKey(scope) });
    },
  });

  if (session === null) {
    return null;
  }

  const items = list.data ?? [];
  const serverError = asMutationError(create.error) ?? asMutationError(revoke.error);

  function dismissSecret() {
    setSecret(null);
    create.reset();
    forgetSecrets(queryClient);
  }

  return (
    <section className="panel">
      <h1>Claves API</h1>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          setClientError(null);
          setSuccess(null);
          const scopes = SCOPES.map((item) => item.id).filter((id) => selected[id] === true);
          if (scopes.length === 0) {
            setClientError("Selecciona al menos un permiso.");
            return;
          }
          create.mutate(scopes);
        }}
      >
        <fieldset>
          <legend>Permisos</legend>
          {SCOPES.map((item) => (
            <label key={item.id}>
              <input
                type="checkbox"
                checked={selected[item.id] === true}
                onChange={(event) => {
                  setSelected((current) => ({ ...current, [item.id]: event.target.checked }));
                }}
              />
              {item.label}
            </label>
          ))}
        </fieldset>
        <button type="submit" disabled={create.isPending}>
          Crear clave
        </button>
      </form>
      {clientError ? <p role="alert">{clientError}</p> : null}
      {serverError ? <ApiAlert error={serverError} /> : null}
      {success ? <p role="status">{success}</p> : null}
      {list.isPending ? <p role="status">Cargando claves</p> : null}
      {list.isError ? <ApiAlert error={asApiError(list.error)} /> : null}
      {list.isSuccess && items.length === 0 ? <p>Todavía no hay claves.</p> : null}
      {items.length > 0 ? (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Prefijo</th>
                <th>Permisos</th>
                <th>Alta</th>
                <th>Estado</th>
                <th>Revocación</th>
                <th>Acciones</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>{item.prefix}</td>
                  <td>{item.scopes.join(", ")}</td>
                  <td>{item.createdAt}</td>
                  <td>{statusLabel(item.status)}</td>
                  <td>{item.revokedAt ?? "—"}</td>
                  <td>
                    {item.revokedAt === null ? (
                      <button type="button" onClick={() => setConfirming(item)}>
                        Revocar {item.prefix}
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {secret !== null ? (
        <SecretDialog title="Secreto de la clave" secretLabel="Secreto" secret={secret} onClose={dismissSecret} />
      ) : null}
      {confirming ? (
        <ConfirmDialog
          title={`Revocar ${confirming.prefix}`}
          effect="La clave dejará de autorizar solicitudes."
          confirmLabel="Confirmar revocación"
          busy={revoke.isPending}
          onConfirm={() => {
            setSuccess(null);
            revoke.mutate(confirming.id);
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
