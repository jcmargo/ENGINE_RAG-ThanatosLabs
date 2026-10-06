import { NavLink, Outlet } from "react-router";

import { useAuth } from "../auth/auth-context.ts";

const PRINCIPAL_LABELS: Record<string, string> = {
  member: "Miembro",
  tenant_admin: "Administrador",
  provider_admin: "Proveedor",
  api_key: "Clave",
};

export function ProviderShell() {
  const { session, logout } = useAuth();
  if (session === null) {
    return null;
  }
  const label = PRINCIPAL_LABELS[session.principalType] ?? session.principalType;
  const identity = session.userId === null ? label : `${label} ${session.userId}`;

  return (
    <div className="shell">
      <aside className="sidebar">
        <p className="brand">ThanatosLabs</p>
        <nav aria-label="Proveedor" className="admin-nav">
          <NavLink to="/provider/tenants">Empresas</NavLink>
          <NavLink to="/provider/usage">Uso global</NavLink>
        </nav>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <p>Sesión: {identity}</p>
          <button type="button" onClick={logout}>
            Cerrar sesión
          </button>
        </header>
        <main>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
