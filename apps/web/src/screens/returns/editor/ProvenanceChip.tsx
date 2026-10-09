import type { Provenance } from "@agentledger/contracts";

import { boxLabel } from "./labels";
import styles from "../returns.module.css";

export interface ProvenanceDescription {
  /** Short, for the chip. */
  summary: string;
  /** Full, for the selected-field panel. */
  detail: string;
  className: string;
  /** The document to open beside the field, when there is one. */
  document: { id: string; box: string | null | undefined } | null;
}

/** Words for a provenance entry (returns/facts.py): a document and box, the preparer, a resolution, a prior return. */
export function describeProvenance(p: Provenance, list: string | null): ProvenanceDescription {
  const source = p.source ?? (p.document_id ? "document" : "preparer");
  switch (source) {
    case "document": {
      const docs = p.documents?.length ? p.documents : null;
      const documentClass = [styles.provDocument, p.confirmed ? "" : styles.provUnconfirmed].join(" ");
      if (docs && docs.length > 1) {
        return {
          summary: `${docs.length} documents · ${p.confirmed ? "confirmed" : "unconfirmed"}`,
          detail: `summed from ${docs.map((d) => `${d.document_id} (${boxLabel(list, d.box)}: ${d.value})`).join(", ")}; ${p.confirmed ? "confirmed by the preparer" : "not confirmed yet"}`,
          className: documentClass,
          document: p.document_id ? { id: p.document_id, box: p.box } : null,
        };
      }
      return {
        summary: `${p.document_id ?? "document"} · ${boxLabel(list, p.box)} · ${p.confirmed ? "confirmed" : "unconfirmed"}`,
        detail: `read from document ${p.document_id ?? "?"}, ${boxLabel(list, p.box)}${p.value !== undefined ? ` (${p.value})` : ""}; ${p.confirmed ? `confirmed by ${p.confirmed_by ?? "the preparer"}` : "not confirmed by the preparer yet"}`,
        className: documentClass,
        document: p.document_id ? { id: p.document_id, box: p.box } : null,
      };
    }
    case "preparer":
      return {
        summary: p.previous_document
          ? `preparer · was ${p.previous_value ?? "—"} (${p.previous_document})`
          : "preparer",
        detail: p.previous_document
          ? `entered by ${p.edited_by ?? "the preparer"} over the document's value: ${p.previous_document} said ${p.previous_value ?? "—"}; populate again to raise the question`
          : `entered by ${p.edited_by ?? "the preparer"}`,
        className: styles.provPreparer ?? "",
        document: p.previous_document ? { id: p.previous_document, box: null } : null,
      };
    case "resolution":
      return {
        summary: `resolved · ${p.document_id ?? "document"} · ${boxLabel(list, p.box)}`,
        detail: `a fact conflict was resolved by ${p.resolved_by ?? "a person"} taking document ${p.document_id ?? "?"}'s value (${boxLabel(list, p.box)})`,
        className: styles.provResolution ?? "",
        document: p.document_id ? { id: p.document_id, box: p.box } : null,
      };
    case "return":
      return {
        summary: `from return ${p.return_id ?? "?"} v${p.version ?? "?"}`,
        detail: `rolled forward from return ${p.return_id ?? "?"} version ${p.version ?? "?"} (${p.box ?? ""})${p.confirmed ? ", confirmed" : ", not confirmed yet"}`,
        className: styles.provDocument ?? "",
        document: null,
      };
    default:
      return { summary: source, detail: source, className: styles.provPreparer ?? "", document: null };
  }
}

/** The chip beside a field: press it to see the document (and the field's history) beside the field. */
export function ProvenanceChip({
  id,
  provenance,
  list,
  selected,
  onSelect,
}: {
  id: string;
  provenance: Provenance;
  list: string | null;
  selected: boolean;
  onSelect: () => void;
}) {
  const d = describeProvenance(provenance, list);
  return (
    <button
      type="button"
      id={id}
      className={[styles.provChip, d.className].join(" ")}
      aria-pressed={selected}
      title={d.detail}
      onClick={onSelect}
    >
      {d.summary}
    </button>
  );
}
