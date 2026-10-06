import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  createTenant,
  listTenants,
  reactivateTenant,
  suspendTenant,
  type TenantItem,
} from "../../api/admin.ts";
import { asApiError, type ApiError } from "../../api/http.ts";
import { useAuth } from "../auth/auth-context.ts";
import { ApiAlert } from "../admin/components/ApiAlert.tsx";
import { ConfirmDialog } from "../admin/components/ConfirmDialog.tsx";
import { SecretDialog } from "../admin/components/SecretDialog.tsx";
import { forgetSecrets } from "../admin/secrets.ts";

function tenantStatusLabel(status: string): string {
  if (status === "active") return "Activa";
  if (status === "suspended") return "Suspendida";
  if (status === "deleting") return "Eliminando";
  return status;
}

export function TenantsPage() {
  const { session } = useAuth();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [adminEmail, setAdminEmail] = useState("");
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [token, setToken] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<{ tenant: TenantItem; action: "suspend" | "reactivate" } | null>(
    null,
  );
  const [success, setSuccess] = useState<string | null>(null);

  const list = useInfiniteQuery({
    queryKey: ["provider", "tenants", status],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => listTenants(status.length === 0 ? null : status, pageParam),
    getNextPageParam: (last) => (last.nextCursor === null ? undefined : last.nextCursor),
    enabled: session?.principalType === "provider_admin",
  });
  const create = useMutation({
    mutationFn: () => createTenant({ name: name.trim(), slug: slug.trim(), adminEmail: adminEmail.trim() }),
    onSuccess: async (created) => {
      setToken(created.activationToken);
      setName("");
      setSlug("");
      setAdminEmail("");
      setSuccess("Empresa creada.");
      await queryClient.invalidateQueries({ queryKey: ["provider", "tenants"] });
    },
  });
  const suspend = useMutation({
    mutationFn: (tenantId: string) => suspendTenant(tenantId),
    onSuccess: async () => {
      setConfirming(null);
      setSuccess("Empresa suspendida.");
      await queryClient.invalidateQueries({ queryKey: ["provider", "tenants"] });
    },
  });
  const reactivate = useMutation({
    mutationFn: (tenantId: string) => reactivateTenant(tenantId),
    onSuccess: async () => {
      setConfirming(null);
      setSuccess("Empresa reactivada.");
      await queryClient.invalidateQueries({ queryKey: ["provider", "tenants"] });
    },
  });

  const items = list.data?.pages.flatMap((page) => page.items) ?? [];
  const needle = search.trim().toLowerCase();
  const visible = items.filter((item) => {
    if (needle.length === 0) {
      return true;
    }
    return item.name.toLowerCase().includes(needle) || item.slug.toLowerCase().includes(needle);
  });
  const serverError =
    asMutationError(create.error) ?? asMutationError(suspend.error) ?? asMutationError(reactivate.error);

  function dismissToken() {
    setToken(null);
    create.reset();
    forgetSecrets(queryClient);
  }

  function confirmAction() {
    if (confirming === null) {
      return;
    }
    setSuccess(null);
    if (confirming.action === "suspend") {
      suspend.mutate(confirming.tenant.id);
      return;
    }
    reactivate.mutate(confirming.tenant.id);
  }

  return (
    <section className="panel">
      <h1>Empresas</h1>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          setSuccess(null);
          create.mutate();
        }}
      >
        <label htmlFor="tenant-name">Nombre</label>
        <input id="tenant-name" required value={name} onChange={(event) => setName(event.target.value)} />
        <label htmlFor="tenant-slug">Slug</label>
        <input id="tenant-slug" required value={slug} onChange={(event) => setSlug(event.target.value)} />
        <label htmlFor="tenant-admin-email">Correo del administrador</label>
        <input
          id="tenant-admin-email"
          type="email"
          required
          value={adminEmail}
          onChange={(event) => setAdminEmail(event.target.value)}
        />
        <button type="submit" disabled={create.isPending}>
          Crear empresa
        </button>
      </form>
      <form
        onSubmit={(event) => {
          event.preventDefault();
        }}
      >
        <label htmlFor="tenant-status">Estado</label>
        <select id="tenant-status" value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="">Todos</option>
          <option value="active">Activa</option>
          <option value="suspended">Suspendida</option>
          <option value="deleting">Eliminando</option>
        </select>
        <label htmlFor="tenant-search">Buscar</label>
        <input
          id="tenant-search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <p>La búsqueda se aplica a las empresas ya cargadas.</p>
      </form>
      {serverError ? <ApiAlert error={serverError} /> : null}
      {success ? <p role="status">{success}</p> : null}
      {list.isPending ? <p role="status">Cargando empresas</p> : null}
      {list.isError ? <ApiAlert error={asApiError(list.error)} /> : null}
      {list.isSuccess && items.length === 0 ? <p>Todavía no hay empresas.</p> : null}
      {list.isSuccess && items.length > 0 && visible.length === 0 ? (
        <p>Ninguna empresa coincide con el filtro.</p>
      ) : null}
      {visible.length > 0 ? (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Nombre</th>
                <th>Slug</th>
                <th>Estado</th>
                <th>Acciones</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((item) => (
                <tr key={item.id}>
                  <td>{item.name}</td>
                  <td>{item.slug}</td>
                  <td>{tenantStatusLabel(item.status)}</td>
                  <td>
                    {item.status === "active" ? (
                      <button
                        type="button"
                        onClick={() => setConfirming({ tenant: item, action: "suspend" })}
                      >
                        Suspender {item.name}
                      </button>
                    ) : null}
                    {item.status === "suspended" ? (
                      <button
                        type="button"
                        onClick={() => setConfirming({ tenant: item, action: "reactivate" })}
                      >
                        Reactivar {item.name}
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {list.hasNextPage ? (
        <button type="button" onClick={() => void list.fetchNextPage()} disabled={list.isFetchingNextPage}>
          Cargar más empresas
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
          title={
            confirming.action === "suspend"
              ? `Suspender ${confirming.tenant.name}`
              : `Reactivar ${confirming.tenant.name}`
          }
          effect={
            confirming.action === "suspend"
              ? "Sus usuarios no podrán acceder en la siguiente solicitud."
              : "La empresa volverá a aceptar sesiones."
          }
          confirmLabel={confirming.action === "suspend" ? "Confirmar suspensión" : "Confirmar reactivación"}
          busy={suspend.isPending || reactivate.isPending}
          onConfirm={confirmAction}
          onCancel={() => setConfirming(null)}
        />
      ) : null}
    </section>
  );
}

function asMutationError(error: unknown): ApiError | null {
  return error === null || error === undefined ? null : asApiError(error);
}
