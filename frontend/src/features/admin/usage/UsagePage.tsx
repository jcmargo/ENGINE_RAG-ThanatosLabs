import { useQuery } from "@tanstack/react-query";

import { dailyUsage, type UsageTotals } from "../../../api/admin.ts";
import { asApiError } from "../../../api/http.ts";
import type { Session } from "../../../api/session.ts";
import { UsageTable } from "../../usage/UsageTable.tsx";
import { sumUsage } from "../../usage/sum.ts";
import { useAuth } from "../../auth/auth-context.ts";
import { ApiAlert } from "../components/ApiAlert.tsx";

function scopeOf(session: Session): string {
  return session.userId ?? session.tenantId ?? "session";
}

export function UsagePage() {
  const { session } = useAuth();
  const scope = session === null ? "session" : scopeOf(session);
  const usage = useQuery({
    queryKey: ["session", scope, "usage"],
    queryFn: () => dailyUsage(),
    enabled: session !== null,
  });

  if (session === null) {
    return null;
  }

  const items = usage.data ?? [];
  const rows: { key: string; label: string; totals: UsageTotals }[] = items.map((item) => ({
    key: item.day,
    label: item.day,
    totals: item,
  }));
  if (items.length > 0) {
    rows.push({ key: "total", label: "Total", totals: sumUsage(items) });
  }

  return (
    <section className="panel">
      <h1>Uso</h1>
      {usage.isPending ? <p role="status">Cargando uso</p> : null}
      {usage.isError ? <ApiAlert error={asApiError(usage.error)} /> : null}
      {usage.isSuccess && items.length === 0 ? <p>Todavía no hay uso registrado.</p> : null}
      {rows.length > 0 ? <UsageTable rows={rows} /> : null}
    </section>
  );
}
