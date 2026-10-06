import { createContext, useContext } from "react";

import type { ApiError } from "../../api/http.ts";
import type { LoginInput } from "../../api/http.ts";
import type { Session } from "../../api/session.ts";

export type AuthStatus = "checking" | "anonymous" | "authenticated";

export type AuthContextValue = {
  status: AuthStatus;
  session: Session | null;
  bootError: ApiError | null;
  login: (input: LoginInput) => Promise<void>;
  logout: () => void;
};

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (value === null) {
    throw new Error("useAuth debe usarse dentro de AuthProvider");
  }
  return value;
}
