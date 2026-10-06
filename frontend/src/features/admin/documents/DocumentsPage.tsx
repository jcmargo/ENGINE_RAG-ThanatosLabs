import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import {
  deleteDocument,
  retryDocument,
  uploadDocument,
  uploadDocumentVersion,
  listDocuments,
  type DocumentItem,
} from "../../../api/admin.ts";
import { asApiError, type ApiError } from "../../../api/http.ts";
import type { Session } from "../../../api/session.ts";
import { useAuth } from "../../auth/auth-context.ts";
import { ApiAlert } from "../components/ApiAlert.tsx";
import { ConfirmDialog } from "../components/ConfirmDialog.tsx";
import { isPendingStatus, uploadProblem } from "./limits.ts";
import { nextPollDelay, type PollClock } from "./poll.ts";

const ACCEPT = ".pdf,.docx,.txt,.md,.markdown";

function scopeOf(session: Session): string {
  return session.userId ?? session.tenantId ?? "session";
}

function documentKey(scope: string) {
  return ["session", scope, "documents"] as const;
}

function statusLabel(status: string): string {
  if (status === "queued") return "En cola";
  if (status === "processing") return "Procesando";
  if (status === "ready") return "Listo";
  if (status === "failed") return "Falló";
  if (status === "deleting") return "Eliminando";
  if (status === "deleted") return "Eliminado";
  if (status === "superseded") return "Reemplazado";
  return status;
}

function canDelete(status: string): boolean {
  return status === "queued" || status === "processing" || status === "ready" || status === "failed";
}

function canVersion(status: string): boolean {
  return status === "ready" || status === "failed";
}

export function DocumentsPage() {
  const { session } = useAuth();
  const queryClient = useQueryClient();
  const clock = useRef<PollClock>({ updatedAt: 0, attempt: 0 });
  const [clientError, setClientError] = useState<string | null>(null);
  const [serverError, setServerError] = useState<ApiError | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [upload, setUpload] = useState<{
    name: string;
    index: number;
    count: number;
    loaded: number;
    total: number;
  } | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<DocumentItem | null>(null);

  const scope = session === null ? "session" : scopeOf(session);
  const list = useInfiniteQuery({
    queryKey: documentKey(scope),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => listDocuments(pageParam),
    getNextPageParam: (last) => (last.nextCursor === null ? undefined : last.nextCursor),
    enabled: session !== null,
    refetchIntervalInBackground: true,
    refetchInterval: (query) => {
      const pages = query.state.data?.pages;
      const pending = pages?.some((page) => page.items.some((item) => isPendingStatus(item.status))) === true;
      return nextPollDelay(pending, clock.current, query.state.dataUpdatedAt);
    },
  });

  if (session === null) {
    return null;
  }

  const items = list.data?.pages.flatMap((page) => page.items) ?? [];

  function clearNotices() {
    setClientError(null);
    setServerError(null);
    setSuccess(null);
  }

  async function sendFile(file: File, documentId: string) {
    const problem = uploadProblem(file);
    if (problem !== null) {
      clearNotices();
      setClientError(problem);
      return;
    }
    clearNotices();
    setUpload({ name: file.name, index: 1, count: 1, loaded: 0, total: file.size });
    try {
      await uploadDocumentVersion(documentId, file, (loaded, total) => {
        setUpload({ name: file.name, index: 1, count: 1, loaded, total });
      });
      setSuccess("Nueva versión cargada.");
      await queryClient.invalidateQueries({ queryKey: documentKey(scope) });
    } catch (caught) {
      setServerError(asApiError(caught));
    } finally {
      setUpload(null);
    }
  }

  async function sendFiles(files: readonly File[]) {
    if (files.length === 0) {
      return;
    }
    clearNotices();
    const many = files.length > 1;
    const problems: string[] = [];
    let uploaded = 0;
    let serverFailure: ApiError | null = null;
    for (const [index, file] of files.entries()) {
      const problem = uploadProblem(file);
      if (problem !== null) {
        problems.push(many ? `${file.name}: ${problem}` : problem);
        continue;
      }
      setUpload({
        name: file.name,
        index: index + 1,
        count: files.length,
        loaded: 0,
        total: file.size,
      });
      try {
        await uploadDocument(file, (loaded, total) => {
          setUpload({
            name: file.name,
            index: index + 1,
            count: files.length,
            loaded,
            total,
          });
        });
        uploaded += 1;
        await queryClient.invalidateQueries({ queryKey: documentKey(scope) });
      } catch (caught) {
        const error = asApiError(caught);
        serverFailure = error;
        if (many) {
          problems.push(`${file.name}: ${error.message}`);
        }
      }
    }
    setUpload(null);
    if (uploaded === 1) {
      setSuccess("Documento cargado.");
    } else if (uploaded > 1) {
      setSuccess(`${uploaded} documentos cargados.`);
    }
    if (!many && serverFailure !== null) {
      setServerError(serverFailure);
    }
    if (problems.length > 0) {
      setClientError(problems.join(" "));
    }
  }

  async function retry(item: DocumentItem) {
    clearNotices();
    setPendingId(item.id);
    try {
      await retryDocument(item.id, item.versionId);
      setSuccess("Reintento solicitado.");
      await queryClient.invalidateQueries({ queryKey: documentKey(scope) });
    } catch (caught) {
      setServerError(asApiError(caught));
    } finally {
      setPendingId(null);
    }
  }

  async function confirmDelete() {
    if (confirming === null) {
      return;
    }
    clearNotices();
    setPendingId(confirming.id);
    try {
      await deleteDocument(confirming.id);
      setSuccess("El documento pasó a eliminación.");
      setConfirming(null);
      await queryClient.invalidateQueries({ queryKey: documentKey(scope) });
    } catch (caught) {
      setServerError(asApiError(caught));
    } finally {
      setPendingId(null);
    }
  }

  return (
    <section className="panel">
      <h1>Documentos</h1>
      <form
        onSubmit={(event) => {
          event.preventDefault();
        }}
      >
        <label htmlFor="document-file">Archivos</label>
        <input
          id="document-file"
          type="file"
          accept={ACCEPT}
          multiple
          disabled={upload !== null}
          onChange={(event) => {
            const selected = event.target.files;
            const files = selected === null ? [] : Array.from(selected);
            event.target.value = "";
            if (files.length > 0) {
              void sendFiles(files);
            }
          }}
        />
        <p>Puedes elegir varios archivos a la vez. Cada uno se agrega a la lista.</p>
      </form>
      {upload ? (
        <div>
          <progress value={upload.loaded} max={Math.max(upload.total, 1)} aria-label="Progreso de la carga" />
          <p role="status">
            {upload.count > 1 ? `Archivo ${upload.index} de ${upload.count}: ${upload.name}. ` : null}
            {upload.loaded} de {upload.total} bytes
          </p>
        </div>
      ) : null}
      {clientError ? <p role="alert">{clientError}</p> : null}
      {serverError ? <ApiAlert error={serverError} /> : null}
      {success ? <p role="status">{success}</p> : null}
      {list.isPending ? <p role="status">Cargando documentos</p> : null}
      {list.isError ? <ApiAlert error={asApiError(list.error)} /> : null}
      {list.isSuccess && items.length === 0 ? <p>Todavía no hay documentos.</p> : null}
      {items.length > 0 ? (
        <div className="table-wrap">
          <table className="data documents-table">
            <thead>
              <tr>
                <th>Nombre</th>
                <th>Estado</th>
                <th>Versión</th>
                <th>Tamaño</th>
                <th>Fragmentos</th>
                <th>Tipo</th>
                <th>Error</th>
                <th>Acciones</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td className="cell-name" title={item.displayName}>
                    {item.displayName}
                  </td>
                  <td>
                    {statusLabel(item.status)}
                    {isPendingStatus(item.status) ? ` (${item.progress}%)` : ""}
                  </td>
                  <td>Versión {item.version}</td>
                  <td>{item.byteSize} bytes</td>
                  <td>{item.chunkCount} fragmentos</td>
                  <td>{item.mimeType}</td>
                  <td>{item.publicErrorCode === null ? "—" : item.publicErrorCode}</td>
                  <td className="cell-actions">
                    <div className="actions">
                      {item.status === "failed" ? (
                        <button
                          type="button"
                          aria-label={`Reintentar ${item.displayName}`}
                          onClick={() => void retry(item)}
                          disabled={pendingId === item.id}
                        >
                          Reintentar
                        </button>
                      ) : null}
                      {canVersion(item.status) ? (
                        <label className="file-action">
                          Nueva versión
                          <input
                            className="file-action-input"
                            type="file"
                            accept={ACCEPT}
                            aria-label={`Nueva versión de ${item.displayName}`}
                            title="Sustituye solo este documento"
                            onChange={(event) => {
                              const file = event.target.files?.[0];
                              event.target.value = "";
                              if (file !== undefined) {
                                void sendFile(file, item.id);
                              }
                            }}
                          />
                        </label>
                      ) : null}
                      {canDelete(item.status) ? (
                        <button
                          type="button"
                          aria-label={`Eliminar ${item.displayName}`}
                          onClick={() => setConfirming(item)}
                        >
                          Eliminar
                        </button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {list.hasNextPage ? (
        <button type="button" onClick={() => void list.fetchNextPage()} disabled={list.isFetchingNextPage}>
          Cargar más documentos
        </button>
      ) : null}
      {confirming ? (
        <ConfirmDialog
          title={`Eliminar ${confirming.displayName}`}
          effect="El documento pasará a eliminación y dejará de usarse en las respuestas."
          confirmLabel="Confirmar eliminación"
          busy={pendingId === confirming.id}
          onConfirm={() => void confirmDelete()}
          onCancel={() => setConfirming(null)}
        />
      ) : null}
    </section>
  );
}
