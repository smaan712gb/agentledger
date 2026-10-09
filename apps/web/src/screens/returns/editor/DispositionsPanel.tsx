import { isApiError, type ClientDocument, type DispositionKind, type ReturnDetail } from "@agentledger/contracts";
import { useMutation } from "@tanstack/react-query";
import { useId, useState } from "react";

import { api } from "../../../api";
import { Button } from "../../../ui/Button";
import { Chip } from "../../../ui/Chip";
import { Empty } from "../../../ui/states";
import { useToast } from "../../../ui/Toast";
import screens from "../../screens.module.css";
import { reliedOn } from "../returnState";
import type { ReturnWorkspace } from "../useReturnWorkspace";
import { PRIOR_YEAR_RETURN, TAX_FORMS } from "./rules";
import styles from "../returns.module.css";

/** The filed tax forms of the return's year (and the prior year's filed return), as store.py `unaccounted_documents` sees them. */
export function yearDocuments(detail: ReturnDetail, documents: ClientDocument[]): ClientDocument[] {
  const year = detail.return.tax_year;
  return documents.filter(
    (d) =>
      d.status === "filed" &&
      typeof d.doc_type === "string" &&
      TAX_FORMS.includes(d.doc_type) &&
      (d.tax_year === year || d.tax_year === null || (d.tax_year === year - 1 && d.doc_type === PRIOR_YEAR_RETURN)),
  );
}

/**
 * Every filed document of the year: on the return (relied on), accounted for (entered by hand, or not applicable, with
 * a reason), or neither, which blocks review until a person decides. The API lists dispositions only in its answer to
 * one, so the panel knows the ones recorded in this session (docs/WEB.md section 4).
 */
export function DispositionsPanel({
  ws,
  detail,
  disabled,
  onOpenDocument,
}: {
  ws: ReturnWorkspace;
  detail: ReturnDetail;
  disabled: string | undefined;
  onOpenDocument: (documentId: string) => void;
}) {
  const docs = yearDocuments(detail, ws.documents);
  const used = reliedOn(detail.inputs ?? {}, detail.provenance ?? {});
  const known = new Map(ws.dispositions.map((d) => [d.document_id, d]));
  if (!docs.length) {
    return (
      <Empty title="No documents for the year">
        <p>Filed tax forms of {detail.return.tax_year} appear here once they are on file.</p>
      </Empty>
    );
  }
  return (
    <div>
      {docs.map((d) => {
        const disposition = known.get(d.id);
        return (
          <div key={d.id} className={styles.docRow} data-testid={`year-doc-${d.id}`}>
            <span>
              <strong>{d.original_name}</strong>
              <div className={screens.meta}>
                {d.doc_type} {d.tax_year ?? "(year unknown)"} · <code>{d.id}</code>
              </div>
            </span>
            <span className={screens.spacer} />
            {used.has(d.id) ? (
              <Chip tone="good">on the return</Chip>
            ) : disposition ? (
              <Chip tone="info" title={disposition.note}>
                {disposition.disposition.replaceAll("_", " ")}
              </Chip>
            ) : (
              <Chip tone="warn">not on the return</Chip>
            )}
            <Button size="sm" tone="ghost" onClick={() => onOpenDocument(d.id)}>
              Show
            </Button>
            {!used.has(d.id) && !disposition ? <DispositionForm ws={ws} documentId={d.id} disabled={disabled} /> : null}
          </div>
        );
      })}
      {ws.moreDocuments ? <p className={screens.hint}>Only the 50 most recent documents are listed.</p> : null}
    </div>
  );
}

function DispositionForm({
  ws,
  documentId,
  disabled,
}: {
  ws: ReturnWorkspace;
  documentId: string;
  disabled: string | undefined;
}) {
  const { toast } = useToast();
  const ids = { kind: useId(), note: useId() };
  const [kind, setKind] = useState<DispositionKind>("entered_by_hand");
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const record = useMutation({
    mutationFn: () => api.returns.disposition(ws.rid, documentId, { disposition: kind, note: note.trim() }),
    onSuccess: async (list) => {
      ws.setDispositions(list);
      toast(`${documentId} accounted for (${kind.replaceAll("_", " ")}).`, "success");
      await ws.invalidate();
    },
    onError: (err) => setProblem(isApiError(err) ? err.message : "The disposition could not be recorded."),
  });
  return (
    <div style={{ flexBasis: "100%" }}>
      <div className={styles.mapRow}>
        <div className={styles.field}>
          <label htmlFor={ids.kind} className={styles.label}>
            Account for it as
          </label>
          <select
            id={ids.kind}
            className={styles.control}
            value={kind}
            onChange={(e) => setKind(e.target.value === "not_applicable" ? "not_applicable" : "entered_by_hand")}
            disabled={Boolean(disabled)}
          >
            <option value="entered_by_hand">entered by hand</option>
            <option value="not_applicable">not applicable</option>
          </select>
        </div>
        <div className={styles.field}>
          <label htmlFor={ids.note} className={styles.label}>
            Reason (at least ten characters)
          </label>
          <input
            id={ids.note}
            className={styles.control}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            disabled={Boolean(disabled)}
            aria-invalid={problem ? true : undefined}
            aria-describedby={problem ? `${ids.note}-problem` : undefined}
          />
        </div>
        <Button
          size="sm"
          busy={record.isPending}
          disabledReason={disabled ?? (note.trim().length < 10 ? "say why, in at least ten characters" : undefined)}
          onClick={() => {
            setProblem(null);
            record.mutate();
          }}
        >
          Record
        </Button>
      </div>
      {problem ? (
        <div id={`${ids.note}-problem`} className={styles.error} role="alert">
          {problem}
        </div>
      ) : null}
    </div>
  );
}
