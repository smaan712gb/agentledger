import { isApiError, type ConflictChoice, type FactConflict } from "@agentledger/contracts";
import { useMutation } from "@tanstack/react-query";
import { useId, useState } from "react";

import { api } from "../../../api";
import { Button } from "../../../ui/Button";
import { Empty } from "../../../ui/states";
import { useToast } from "../../../ui/Toast";
import screens from "../../screens.module.css";
import { boxLabel, humanise } from "./labels";
import { leafOf, listOf, locateAnchor } from "./paths";
import { displayValue } from "./schema";
import styles from "../returns.module.css";

export type ConflictKind = "disagreement" | "missing" | "orphan";

export function conflictKind(c: FactConflict): ConflictKind {
  if (c.anchor.startsWith("missing:")) return "missing";
  if (c.anchor.startsWith("orphan:")) return "orphan";
  return "disagreement";
}

// The API's own refusals (returns/store.py `resolve_conflict`), shown as the disabled reason before it has to say them.
const TAKE_REFUSED: Record<Exclude<ConflictKind, "disagreement">, string> = {
  missing: "the document has no value here; enter the amount, then keep it",
  orphan: "that document no longer belongs to this return: remove the item, or keep it with a reason",
};

/**
 * Open fact conflicts (returns/facts.py): the current value stays until a person keeps it or takes the document's
 * value, which re-reads the documents first. Missing amounts and orphaned items are questions only "keep" answers,
 * after the person entered the amount or decided to keep the item with a reason.
 */
export function ConflictsPanel({
  rid,
  conflicts,
  inputs,
  disabled,
  onResolved,
  onOpenDocument,
  onLocate,
}: {
  rid: string;
  conflicts: FactConflict[];
  inputs: Record<string, unknown>;
  disabled: string | undefined;
  onResolved: () => Promise<unknown>;
  onOpenDocument: (c: FactConflict) => void;
  onLocate: (path: string) => void;
}) {
  const { toast } = useToast();
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [problems, setProblems] = useState<Record<number, string>>({});
  const resolve = useMutation({
    mutationFn: ({ id, choice, note }: { id: number; choice: ConflictChoice; note: string }) =>
      api.returns.resolveConflict(rid, id, note ? { choice, note } : { choice }),
    onSuccess: async (r, { choice }) => {
      toast(choice === "document" ? "The document's value was taken." : "Your value was kept.", "success");
      toast(`${r.open} open conflict(s) left.`);
      await onResolved();
    },
    onError: (err, { id }) => {
      setProblems((prev) => ({ ...prev, [id]: isApiError(err) ? err.message : "The conflict could not be resolved." }));
    },
  });

  if (!conflicts.length) {
    return (
      <Empty title="No open conflicts">
        <p>Populating from documents raises one whenever a document disagrees with what the return holds.</p>
      </Empty>
    );
  }
  return (
    <ul className={styles.checklist} aria-label="Open conflicts">
      {conflicts.map((c) => {
        const kind = conflictKind(c);
        const path = locateAnchor(c.anchor, inputs);
        const list = listOf(path ?? "");
        const note = notes[c.id] ?? "";
        const busyWith = (choice: ConflictChoice) =>
          resolve.isPending && resolve.variables.id === c.id && resolve.variables.choice === choice;
        const noteTooShort = kind === "orphan" && note.trim().length < 10;
        return (
          <li key={c.id} className={styles.conflict} data-testid={`conflict-${c.id}`}>
            <strong>{humanise(leafOf(c.anchor))}</strong> <code className={styles.code}>{c.anchor}</code>
            {kind === "disagreement" ? (
              <dl className={styles.values}>
                <dt>The return holds</dt>
                <dd>
                  {displayValue(c.current_value) || "—"} <span className={screens.meta}>({c.current_source})</span>
                </dd>
                <dt>The document says</dt>
                <dd>
                  {displayValue(c.proposed_value) || "—"}{" "}
                  <span className={screens.meta}>
                    ({c.document_id}, {boxLabel(list, c.box)})
                  </span>
                </dd>
              </dl>
            ) : kind === "missing" ? (
              <p>
                {boxLabel(list, c.box)} of {c.document_id}: the amount is missing on the return and is never taken as
                zero. Enter it from the document (0 only if the document shows 0), then keep it.
              </p>
            ) : (
              <p>
                Document {c.document_id} no longer belongs to this return (moved, re-dated or deleted). Remove the item,
                or keep it with a reason of at least ten characters.
              </p>
            )}
            {kind !== "missing" ? (
              <NoteField
                id={c.id}
                value={note}
                required={kind === "orphan"}
                onChange={(v) => setNotes((prev) => ({ ...prev, [c.id]: v }))}
              />
            ) : null}
            <div className={screens.actions}>
              <Button
                size="sm"
                tone="primary"
                busy={busyWith("document")}
                disabledReason={disabled ?? (kind !== "disagreement" ? TAKE_REFUSED[kind] : undefined)}
                onClick={() => resolve.mutate({ id: c.id, choice: "document", note: note.trim() })}
              >
                Take the document's value
              </Button>
              <Button
                size="sm"
                busy={busyWith("keep")}
                disabledReason={
                  disabled ??
                  (noteTooShort ? "say why the item stays although its document left the return" : undefined)
                }
                onClick={() => resolve.mutate({ id: c.id, choice: "keep", note: note.trim() })}
              >
                {kind === "missing" ? "Keep: I entered it" : kind === "orphan" ? "Keep with this reason" : "Keep mine"}
              </Button>
              <Button size="sm" tone="ghost" onClick={() => onOpenDocument(c)}>
                Show document
              </Button>
              {path ? (
                <Button size="sm" tone="ghost" onClick={() => onLocate(path)}>
                  Go to field
                </Button>
              ) : null}
            </div>
            {problems[c.id] ? (
              <div className={styles.error} role="alert">
                {problems[c.id]}
              </div>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function NoteField({
  id,
  value,
  required,
  onChange,
}: {
  id: number;
  value: string;
  required: boolean;
  onChange: (value: string) => void;
}) {
  const inputId = useId();
  return (
    <div className={styles.field}>
      <label htmlFor={inputId} className={styles.label}>
        {required ? "Reason (required)" : "Note (optional)"}
      </label>
      <input
        id={inputId}
        className={styles.control}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        data-testid={`conflict-note-${id}`}
      />
    </div>
  );
}
