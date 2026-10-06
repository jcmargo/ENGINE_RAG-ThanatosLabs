import type { UsageTotals } from "../../api/admin.ts";

export function UsageTable({ rows }: { rows: { key: string; label: string; totals: UsageTotals }[] }) {
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>Periodo</th>
            <th>Documentos</th>
            <th>Bytes</th>
            <th>Fragmentos</th>
            <th>Preguntas</th>
            <th>Abstenciones</th>
            <th>Errores</th>
            <th>p50 (ms)</th>
            <th>p95 (ms)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <th scope="row">{row.label}</th>
              <td>{row.totals.documents}</td>
              <td>{row.totals.bytes}</td>
              <td>{row.totals.chunks}</td>
              <td>{row.totals.questions}</td>
              <td>{row.totals.abstentions}</td>
              <td>{row.totals.errors}</td>
              <td>{formatLatency(row.totals.p50Ms)}</td>
              <td>{formatLatency(row.totals.p95Ms)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatLatency(value: number | null): string {
  return value === null ? "—" : String(value);
}
