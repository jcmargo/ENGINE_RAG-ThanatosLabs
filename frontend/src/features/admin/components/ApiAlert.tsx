import type { ApiError } from "../../../api/http.ts";

export function ApiAlert({ error }: { error: ApiError }) {
  return (
    <p role="alert">
      {error.message}
      {error.status >= 500 ? ` Identificador: ${error.requestId}.` : null}
    </p>
  );
}
