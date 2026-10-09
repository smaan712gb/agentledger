import { useQuery } from "@tanstack/react-query";

import { formatDateTime } from "../../../lib/format";
import { queries } from "../../../queries";
import { QueryBoundary } from "../../../ui/states";
import screens from "../../screens.module.css";
import { displayValue } from "./schema";
import styles from "../returns.module.css";

/** Every value one anchor has had, oldest first (GET /api/returns/{rid}/facts: append-only). */
export function FactHistory({ rid, anchor }: { rid: string; anchor: string }) {
  const history = useQuery(queries.factHistory(rid, anchor));
  return (
    <section aria-label="Fact history" data-testid="fact-history">
      <h3 className="small">
        History of <code className={styles.code}>{anchor}</code>
      </h3>
      <QueryBoundary
        query={history}
        loadingLabel="Loading history"
        isEmpty={(rows) => rows.length === 0}
        empty={<p className={screens.hint}>No recorded assertion yet: the value was never set on this return.</p>}
      >
        {(rows) => (
          <ol className={styles.timeline}>
            {[...rows].reverse().map((a) => (
              <li key={a.id} className={styles.timelineItem}>
                <span className={screens.meta}>{formatDateTime(a.asserted_at)}</span>
                <span>
                  <strong>{a.source === "removed" ? "removed" : displayValue(a.value) || "—"}</strong>{" "}
                  <span className={screens.meta}>
                    {a.source}
                    {a.source_ref && a.source_ref !== a.asserted_by ? ` ${a.source_ref}` : ""} · by {a.asserted_by}
                    {a.supersedes !== null ? ` · supersedes #${a.supersedes}` : ""}
                  </span>
                </span>
              </li>
            ))}
          </ol>
        )}
      </QueryBoundary>
    </section>
  );
}
