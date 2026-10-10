import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router";

import { activateAccount, asApiError } from "../../../api/http.ts";

export function ActivatePage() {
  const navigate = useNavigate();
  const [step, setStep] = useState<"token" | "password">("token");
  const [token, setToken] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  function onToken(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (token.trim().length === 0) {
      setError("Pega el token de activación.");
      return;
    }
    setError(null);
    setStep("password");
  }

  async function onPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) {
      return;
    }
    if (password.trim().length === 0 || confirmation.trim().length === 0) {
      setError("Escribe la contraseña y confírmala.");
      return;
    }
    if (password !== confirmation) {
      setError("Las contraseñas no coinciden.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      await activateAccount({ token, password });
      navigate("/login", { replace: true, state: { activated: true } });
    } catch (caught) {
      setError(asApiError(caught).message);
      setPending(false);
    }
  }

  return (
    <main className="login">
      <header>
        <p className="brand">ThanatosLabs</p>
        <p className="product">Engine RAG</p>
      </header>
      {step === "token" ? (
        <>
          <h1>Activar cuenta</h1>
          <form onSubmit={onToken}>
            <label htmlFor="activation-token">Token de activación</label>
            <input
              id="activation-token"
              name="activationToken"
              autoComplete="off"
              value={token}
              onChange={(event) => setToken(event.target.value)}
            />
            <button type="submit">Continuar</button>
          </form>
        </>
      ) : (
        <>
          <h1>Crear contraseña</h1>
          <form onSubmit={(event) => void onPassword(event)}>
            <label htmlFor="new-password">Contraseña</label>
            <input
              id="new-password"
              name="newPassword"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
            <label htmlFor="confirm-password">Confirmar contraseña</label>
            <input
              id="confirm-password"
              name="confirmPassword"
              type="password"
              autoComplete="new-password"
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
            />
            <button type="submit" disabled={pending}>
              Crear contraseña
            </button>
          </form>
        </>
      )}
      {pending ? <p role="status">Activando la cuenta</p> : null}
      {error !== null ? <p role="alert">{error}</p> : null}
    </main>
  );
}
