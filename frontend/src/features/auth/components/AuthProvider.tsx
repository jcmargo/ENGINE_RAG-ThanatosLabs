import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { asApiError, login as loginRequest, logoutRequest, restoreSession } from "../../../api/http.ts";
import type { ApiError, LoginInput } from "../../../api/http.ts";
import { setSession, subscribeSession, type Session } from "../../../api/session.ts";
import { AuthContext, type AuthStatus } from "../auth-context.ts";

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<AuthStatus>("checking");
  const [session, setSessionState] = useState<Session | null>(null);
  const [bootError, setBootError] = useState<ApiError | null>(null);
  const seenType = useRef<string | null>(null);

  useEffect(() => {
    return subscribeSession((next) => {
      const previous = seenType.current;
      seenType.current = next?.principalType ?? null;
      setSessionState(next);
      if (next === null) {
        queryClient.clear();
        setStatus("anonymous");
        return;
      }
      if (previous !== null && previous !== next.principalType) {
        queryClient.clear();
      }
      setStatus("authenticated");
    });
  }, [queryClient]);

  useEffect(() => {
    let active = true;
    restoreSession()
      .then((next) => {
        if (!active) {
          return;
        }
        setSessionState(next);
        setStatus(next === null ? "anonymous" : "authenticated");
      })
      .catch((error: unknown) => {
        if (!active) {
          return;
        }
        setBootError(asApiError(error));
        setSession(null);
        setStatus("anonymous");
      });
    return () => {
      active = false;
    };
  }, []);

  const login = useCallback(async (input: LoginInput) => {
    setBootError(null);
    await loginRequest(input);
  }, []);

  const logout = useCallback(() => {
    setSession(null);
    void logoutRequest();
  }, []);

  const value = useMemo(
    () => ({ status, session, bootError, login, logout }),
    [status, session, bootError, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
