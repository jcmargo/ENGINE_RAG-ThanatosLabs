import type { Citation } from "../../../api/http.ts";

export function CitationList({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) {
    return null;
  }
  return (
    <ol className="citations">
      {citations.map((citation, index) => {
        const location =
          citation.section === null
            ? `página ${citation.page}`
            : `página ${citation.page}, sección ${citation.section}`;
        return (
          <li key={`${citation.documentId}-${citation.page}-${citation.section ?? ""}`}>
            <details>
              <summary>
                Cita {index + 1}: {citation.documentName}, {location}
              </summary>
              <p>
                <span className="citation-label">Extracto</span> {citation.excerpt}
              </p>
            </details>
          </li>
        );
      })}
    </ol>
  );
}
