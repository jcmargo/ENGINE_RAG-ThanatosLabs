import { Outlet, Route, Routes } from "react-router";

import { DocumentsPage } from "../features/admin/documents/DocumentsPage.tsx";
import { ApiKeysPage } from "../features/admin/keys/ApiKeysPage.tsx";
import { UsagePage } from "../features/admin/usage/UsagePage.tsx";
import { UsersPage } from "../features/admin/users/UsersPage.tsx";
import { AuthProvider } from "../features/auth/components/AuthProvider.tsx";
import { LoginPage } from "../features/auth/components/LoginPage.tsx";
import {
  EmployeeOnly,
  GuestOnly,
  ProviderOnly,
  RequireAuth,
  TenantAdminOnly,
} from "../features/auth/components/SessionGate.tsx";
import { AppShell } from "../features/chat/components/AppShell.tsx";
import { ChatView } from "../features/chat/components/ChatView.tsx";
import { ProviderShell } from "../features/provider/ProviderShell.tsx";
import { ProviderUsagePage } from "../features/provider/ProviderUsagePage.tsx";
import { TenantsPage } from "../features/provider/TenantsPage.tsx";

export function AppRoutes() {
  return (
    <AuthProvider>
      <Routes>
        <Route
          path="/login"
          element={
            <GuestOnly>
              <LoginPage />
            </GuestOnly>
          }
        />
        <Route element={<RequireAuth><Outlet /></RequireAuth>}>
          <Route element={<EmployeeOnly />}>
            <Route element={<AppShell />}>
              <Route index element={<ChatView />} />
              <Route path="conversations/:conversationId" element={<ChatView />} />
              <Route element={<TenantAdminOnly />}>
                <Route path="admin/documents" element={<DocumentsPage />} />
                <Route path="admin/users" element={<UsersPage />} />
                <Route path="admin/api-keys" element={<ApiKeysPage />} />
                <Route path="admin/usage" element={<UsagePage />} />
              </Route>
            </Route>
          </Route>
          <Route element={<ProviderOnly />}>
            <Route element={<ProviderShell />}>
              <Route path="provider/tenants" element={<TenantsPage />} />
              <Route path="provider/usage" element={<ProviderUsagePage />} />
            </Route>
          </Route>
        </Route>
      </Routes>
    </AuthProvider>
  );
}
