import type { ReactNode } from "react";
import { Navigate, Outlet } from "react-router";

import { useAuth } from "../auth-context.ts";

export function SessionNotice() {
  return <p role="status">Comprobando sesión</p>;
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  if (status === "checking") {
    return <SessionNotice />;
  }
  if (status === "anonymous") {
    return <Navigate to="/login" replace />;
  }
  return children;
}

export function EmployeeOnly() {
  const { session } = useAuth();
  if (session?.principalType === "provider_admin") {
    return <Navigate to="/provider/tenants" replace />;
  }
  return <Outlet />;
}

export function ProviderOnly() {
  const { session } = useAuth();
  if (session?.principalType !== "provider_admin") {
    return <Navigate to="/" replace />;
  }
  return <Outlet />;
}

export function TenantAdminOnly() {
  const { session } = useAuth();
  if (session?.principalType !== "tenant_admin") {
    return <Navigate to="/" replace />;
  }
  return <Outlet />;
}

export function GuestOnly({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  if (status === "checking") {
    return <SessionNotice />;
  }
  if (status === "authenticated") {
    return <Navigate to="/" replace />;
  }
  return children;
}
