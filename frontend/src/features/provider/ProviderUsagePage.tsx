import { useQuery } from "@tanstack/react-query";

import { providerUsage } from "../../api/admin.ts";
import { asApiError } from "../../api/http.ts";
import { useAuth } from "../auth/auth-context.ts";
import { ApiAlert } from "../admin/components/ApiAlert.tsx";
import { UsageTable } from "../usage/UsageTable.tsx";

export function ProviderUsagePage() {
  const { session } = useAuth();
  const usage = useQuery({
    queryKey: ["provider", "usage"],
    queryFn: () => providerUsage(),
    enabled: session?.principalType === "provider_admin",
  });

  return (
    <section className="panel">
      <h1>Uso global</h1>
      {usage.isPending ? <p role="status">Cargando uso</p> : null}
      {usage.isError ? <ApiAlert error={asApiError(usage.error)} /> : null}
      {usage.data ? <UsageTable rows={[{ key: "global", label: "Global", totals: usage.data }]} /> : null}
    </section>
  );
}
