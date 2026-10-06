import { NavLink, Outlet } from "react-router";

import { useAuth } from "../../auth/auth-context.ts";
import { ConversationList } from "./ConversationList.tsx";

const PRINCIPAL_LABELS: Record<string, string> = {
  member: "Miembro",
  tenant_admin: "Administrador",
  provider_admin: "Proveedor",
  api_key: "Clave",
};

export function AppShell() {
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
        {session.principalType === "tenant_admin" ? (
          <nav aria-label="Administración" className="admin-nav">
            <NavLink to="/admin/documents">Documentos</NavLink>
            <NavLink to="/admin/users">Usuarios</NavLink>
            <NavLink to="/admin/api-keys">Claves API</NavLink>
            <NavLink to="/admin/usage">Uso</NavLink>
          </nav>
        ) : null}
        <ConversationList session={session} />
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
