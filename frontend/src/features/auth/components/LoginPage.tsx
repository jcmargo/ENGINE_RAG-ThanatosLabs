import { useState, type FormEvent } from "react";
import { Link, useLocation } from "react-router";

import { asApiError, type ApiError } from "../../../api/http.ts";
import { useAuth } from "../auth-context.ts";

export const ACTIVATION_READY_NOTICE =
  "Ya se puede entrar con la empresa (slug), el correo y la contraseña elegida.";

function showActivationNotice(state: unknown): boolean {
  if (typeof state !== "object" || state === null || !("activated" in state)) {
    return false;
  }
  return state.activated === true;
}

export function LoginPage() {
  const { login, bootError } = useAuth();
  const activated = showActivationNotice(useLocation().state);
  const [tenantSlug, setTenantSlug] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) {
      return;
    }
    setPending(true);
    setError(null);
    try {
      await login({ email, password, tenantSlug });
    } catch (caught) {
      setError(asApiError(caught));
    } finally {
      setPending(false);
    }
  }

  const visibleError = error ?? bootError;

  return (
    <main className="login">
      <header>
        <p className="brand">ThanatosLabs</p>
        <p className="product">Engine RAG</p>
      </header>
      <h1>Iniciar sesión</h1>
      <form onSubmit={(event) => void onSubmit(event)}>
        <label htmlFor="tenant-slug">Empresa</label>
        <input
          id="tenant-slug"
          name="tenantSlug"
          autoComplete="organization"
          value={tenantSlug}
          onChange={(event) => setTenantSlug(event.target.value)}
        />
        <p>Opcional para el proveedor.</p>
        <label htmlFor="email">Correo</label>
        <input
          id="email"
          name="email"
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
        <label htmlFor="password">Contraseña</label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        <button type="submit" disabled={pending}>
          Entrar
        </button>
      </form>
      <p className="account-link">
        <Link to="/activate">Activar cuenta</Link>
      </p>
      {activated ? <p role="status">{ACTIVATION_READY_NOTICE}</p> : null}
      {pending ? <p role="status">Comprobando credenciales</p> : null}
      {visibleError ? (
        <p role="alert">
          {visibleError.message}
          {visibleError.status >= 500 ? ` Identificador: ${visibleError.requestId}` : null}
        </p>
      ) : null}
    </main>
  );
}
