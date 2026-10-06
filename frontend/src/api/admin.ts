/** Cliente de administración. Los secretos no se guardan en la caché de queries. */

import { ApiError, requestEmpty, requestJson, uploadFile } from "./http.ts";

export type DocumentItem = {
  id: string;
  displayName: string;
  status: string;
  version: number;
  byteSize: number;
  mimeType: string;
  createdAt: string;
  updatedAt: string;
  publicErrorCode: string | null;
  chunkCount: number;
  progress: number;
  versionId: string;
};

export type ItemPage<T> = {
  items: T[];
  nextCursor: string | null;
  total: number;
};

export type TenantUser = {
  userId: string;
  email: string;
  role: string;
  status: string;
  createdAt: string;
};

export type Invitation = {
  userId: string;
  email: string;
  role: string;
  activationToken: string;
};

export type ApiKeyItem = {
  id: string;
  prefix: string;
  scopes: string[];
  status: string;
  createdAt: string;
  updatedAt: string;
  revokedAt: string | null;
};

export type CreatedApiKey = {
  id: string;
  prefix: string;
  scopes: string[];
  status: string;
  createdAt: string;
  secret: string;
};

export type UsageTotals = {
  documents: number;
  bytes: number;
  chunks: number;
  questions: number;
  abstentions: number;
  errors: number;
  p50Ms: number | null;
  p95Ms: number | null;
};

export type DailyUsage = UsageTotals & { day: string };

export type TenantItem = {
  id: string;
  name: string;
  slug: string;
  status: string;
  createdAt: string;
  updatedAt: string;
};

export type CreatedTenant = TenantItem & {
  adminUserId: string;
  activationToken: string;
};

export type UploadProgress = (loaded: number, total: number) => void;

export function listDocuments(cursor: string | null): Promise<ItemPage<DocumentItem>> {
  return requestJson(withQuery("/v1/documents", { cursor, limit: 50 }), { method: "GET" }, parseDocumentPage);
}

export function uploadDocument(file: File, onProgress: UploadProgress): Promise<DocumentItem> {
  return uploadFile("/v1/documents", file, onProgress).then((body) => parseDocument(body, crypto.randomUUID()));
}

export function uploadDocumentVersion(
  documentId: string,
  file: File,
  onProgress: UploadProgress,
): Promise<DocumentItem> {
  const path = `/v1/documents/${encodeURIComponent(documentId)}/versions`;
  return uploadFile(path, file, onProgress).then((body) => parseDocument(body, crypto.randomUUID()));
}

export function retryDocument(documentId: string, versionId: string): Promise<DocumentItem> {
  const path = `/v1/documents/${encodeURIComponent(documentId)}/versions/${encodeURIComponent(versionId)}/retry`;
  return requestJson(path, { method: "POST" }, parseDocument);
}

export function deleteDocument(documentId: string): Promise<void> {
  return requestEmpty(`/v1/documents/${encodeURIComponent(documentId)}`, { method: "DELETE" });
}

export function listUsers(cursor: string | null): Promise<ItemPage<TenantUser>> {
  return requestJson(withQuery("/v1/users", { cursor, limit: 50 }), { method: "GET" }, parseUserPage);
}

export function inviteUser(email: string, role: string): Promise<Invitation> {
  return requestJson("/v1/users", { method: "POST", body: { email, role } }, parseInvitation);
}

export function changeUserRole(userId: string, role: string): Promise<TenantUser> {
  return requestJson(
    `/v1/users/${encodeURIComponent(userId)}`,
    { method: "PATCH", body: { role } },
    parseUser,
  );
}

export function deactivateUser(userId: string): Promise<TenantUser> {
  return requestJson(`/v1/users/${encodeURIComponent(userId)}/deactivate`, { method: "POST" }, parseUser);
}

export function listApiKeys(): Promise<ApiKeyItem[]> {
  return requestJson("/v1/api-keys", { method: "GET" }, parseApiKeyList);
}

export function createApiKey(scopes: string[]): Promise<CreatedApiKey> {
  return requestJson("/v1/api-keys", { method: "POST", body: { scopes } }, parseCreatedApiKey);
}

export function revokeApiKey(apiKeyId: string): Promise<void> {
  return requestJson(
    `/v1/api-keys/${encodeURIComponent(apiKeyId)}/revoke`,
    { method: "POST" },
    () => undefined,
  );
}

export function dailyUsage(): Promise<DailyUsage[]> {
  return requestJson("/v1/usage/daily", { method: "GET" }, parseDailyUsage);
}

export function listTenants(
  status: string | null,
  cursor: string | null,
): Promise<ItemPage<TenantItem>> {
  return requestJson(
    withQuery("/v1/provider/tenants", { status, cursor, limit: 50 }),
    { method: "GET" },
    parseTenantPage,
  );
}

export function createTenant(input: {
  name: string;
  slug: string;
  adminEmail: string;
}): Promise<CreatedTenant> {
  return requestJson(
    "/v1/provider/tenants",
    { method: "POST", body: { name: input.name, slug: input.slug, admin_email: input.adminEmail } },
    parseCreatedTenant,
  );
}

export function suspendTenant(tenantId: string): Promise<TenantItem> {
  return requestJson(
    `/v1/provider/tenants/${encodeURIComponent(tenantId)}/suspend`,
    { method: "POST" },
    parseTenant,
  );
}

export function reactivateTenant(tenantId: string): Promise<TenantItem> {
  return requestJson(
    `/v1/provider/tenants/${encodeURIComponent(tenantId)}/reactivate`,
    { method: "POST" },
    parseTenant,
  );
}

export function providerUsage(): Promise<UsageTotals> {
  return requestJson("/v1/provider/usage", { method: "GET" }, parseTotals);
}

function withQuery(
  path: string,
  params: Record<string, string | number | null | undefined>,
): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") {
      continue;
    }
    search.set(key, String(value));
  }
  const query = search.toString();
  return query.length === 0 ? path : `${path}?${query}`;
}

function parseDocumentPage(value: unknown, requestId: string): ItemPage<DocumentItem> {
  return parsePage(value, requestId, parseDocument);
}

function parseDocument(value: unknown, requestId: string): DocumentItem {
  const record = asRecord(value, requestId);
  return {
    id: readString(record, "id", requestId),
    displayName: readString(record, "display_name", requestId),
    status: readString(record, "status", requestId),
    version: readNumber(record, "version", requestId),
    byteSize: readNumber(record, "byte_size", requestId),
    mimeType: readString(record, "mime_type", requestId),
    createdAt: readString(record, "created_at", requestId),
    updatedAt: readString(record, "updated_at", requestId),
    publicErrorCode: readNullableString(record, "public_error_code", requestId),
    chunkCount: readNumber(record, "chunk_count", requestId),
    progress: readNumber(record, "progress", requestId),
    versionId: readString(record, "version_id", requestId),
  };
}

function parseUserPage(value: unknown, requestId: string): ItemPage<TenantUser> {
  return parsePage(value, requestId, parseUser);
}

function parseUser(value: unknown, requestId: string): TenantUser {
  const record = asRecord(value, requestId);
  return {
    userId: readString(record, "user_id", requestId),
    email: readString(record, "email", requestId),
    role: readString(record, "role", requestId),
    status: readString(record, "status", requestId),
    createdAt: readString(record, "created_at", requestId),
  };
}

function parseInvitation(value: unknown, requestId: string): Invitation {
  const record = asRecord(value, requestId);
  return {
    userId: readString(record, "user_id", requestId),
    email: readString(record, "email", requestId),
    role: readString(record, "role", requestId),
    activationToken: readString(record, "activation_token", requestId),
  };
}

function parseApiKeyList(value: unknown, requestId: string): ApiKeyItem[] {
  const record = asRecord(value, requestId);
  if (!Array.isArray(record.items)) {
    throw invalid(requestId);
  }
  return record.items.map((item) => parseApiKey(item, requestId));
}

function parseApiKey(value: unknown, requestId: string): ApiKeyItem {
  const record = asRecord(value, requestId);
  return {
    id: readString(record, "id", requestId),
    prefix: readString(record, "prefix", requestId),
    scopes: readStringArray(record, "scopes", requestId),
    status: readString(record, "status", requestId),
    createdAt: readString(record, "created_at", requestId),
    updatedAt: readString(record, "updated_at", requestId),
    revokedAt: readNullableString(record, "revoked_at", requestId),
  };
}

function parseCreatedApiKey(value: unknown, requestId: string): CreatedApiKey {
  const record = asRecord(value, requestId);
  return {
    id: readString(record, "id", requestId),
    prefix: readString(record, "prefix", requestId),
    scopes: readStringArray(record, "scopes", requestId),
    status: readString(record, "status", requestId),
    createdAt: readString(record, "created_at", requestId),
    secret: readString(record, "secret", requestId),
  };
}

function parseDailyUsage(value: unknown, requestId: string): DailyUsage[] {
  const record = asRecord(value, requestId);
  if (!Array.isArray(record.items)) {
    throw invalid(requestId);
  }
  return record.items.map((item) => {
    const row = asRecord(item, requestId);
    return { ...parseTotals(row, requestId), day: readString(row, "day", requestId) };
  });
}

function parseTenantPage(value: unknown, requestId: string): ItemPage<TenantItem> {
  return parsePage(value, requestId, parseTenant);
}

function parseTenant(value: unknown, requestId: string): TenantItem {
  const record = asRecord(value, requestId);
  return {
    id: readString(record, "id", requestId),
    name: readString(record, "name", requestId),
    slug: readString(record, "slug", requestId),
    status: readString(record, "status", requestId),
    createdAt: readString(record, "created_at", requestId),
    updatedAt: readString(record, "updated_at", requestId),
  };
}

function parseCreatedTenant(value: unknown, requestId: string): CreatedTenant {
  const record = asRecord(value, requestId);
  return {
    ...parseTenant(record, requestId),
    adminUserId: readString(record, "admin_user_id", requestId),
    activationToken: readString(record, "activation_token", requestId),
  };
}

function parseTotals(value: unknown, requestId: string): UsageTotals {
  const record = asRecord(value, requestId);
  return {
    documents: readNumber(record, "documents", requestId),
    bytes: readNumber(record, "bytes", requestId),
    chunks: readNumber(record, "chunks", requestId),
    questions: readNumber(record, "questions", requestId),
    abstentions: readNumber(record, "abstentions", requestId),
    errors: readNumber(record, "errors", requestId),
    p50Ms: readNullableNumber(record, "p50_ms", requestId),
    p95Ms: readNullableNumber(record, "p95_ms", requestId),
  };
}

function parsePage<T>(
  value: unknown,
  requestId: string,
  parseItem: (item: unknown, requestId: string) => T,
): ItemPage<T> {
  const record = asRecord(value, requestId);
  if (!Array.isArray(record.items)) {
    throw invalid(requestId);
  }
  return {
    items: record.items.map((item) => parseItem(item, requestId)),
    nextCursor: readNullableString(record, "next_cursor", requestId),
    total: readNumber(record, "total", requestId),
  };
}

function asRecord(value: unknown, requestId: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw invalid(requestId);
  }
  return value as Record<string, unknown>;
}

function readString(record: Record<string, unknown>, key: string, requestId: string): string {
  const value = record[key];
  if (typeof value !== "string") {
    throw invalid(requestId);
  }
  return value;
}

function readNullableString(
  record: Record<string, unknown>,
  key: string,
  requestId: string,
): string | null {
  const value = record[key];
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value !== "string") {
    throw invalid(requestId);
  }
  return value;
}

function readNumber(record: Record<string, unknown>, key: string, requestId: string): number {
  const value = record[key];
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw invalid(requestId);
  }
  return value;
}

function readNullableNumber(
  record: Record<string, unknown>,
  key: string,
  requestId: string,
): number | null {
  const value = record[key];
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw invalid(requestId);
  }
  return value;
}

function readStringArray(record: Record<string, unknown>, key: string, requestId: string): string[] {
  const value = record[key];
  if (!Array.isArray(value)) {
    throw invalid(requestId);
  }
  const items: string[] = [];
  for (const item of value) {
    if (typeof item !== "string") {
      throw invalid(requestId);
    }
    items.push(item);
  }
  return items;
}

function invalid(requestId: string): ApiError {
  return new ApiError(
    "La respuesta del servidor no tiene la forma esperada.",
    500,
    requestId,
    null,
    "invalid_response",
  );
}
