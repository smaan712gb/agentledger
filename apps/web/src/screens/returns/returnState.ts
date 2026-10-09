/**
 * Pure helpers for the return workspace, mirroring src/agentledger/returns/store.py: which statuses bind the package
 * to an approved hash (editing reopens the return) or freeze it (a filed return is evidence and is never edited), the
 * checklist of what stops a return from moving on (`_blockers`, with stable codes), who submitted it (segregation of
 * duties), and which actions a status permits. The API remains the authority; its 409 text is rendered when the two
 * disagree.
 */

import type {
  ClientDocument,
  Disposition,
  FactConflict,
  Provenance,
  ReturnDetail,
  ReturnEvent,
  ReturnStatus,
  WorkflowEvent,
} from "@agentledger/contracts";

import { isRecord, itemIdentity } from "./editor/paths";
import { PRIOR_YEAR_RETURN, TAX_FORMS, missingAmounts } from "./editor/rules";

/** Statuses whose package is bound to a hash a person approved: a change reopens the return (store.py HASH_BOUND). */
export const HASH_BOUND: readonly ReturnStatus[] = ["approved", "awaiting_signature", "signed", "release_approved"];
/** A filed (or possibly filed) return is evidence of what was sent: never edited or recomputed (store.py FROZEN). */
export const FROZEN: readonly ReturnStatus[] = [
  "transmitted",
  "accepted",
  "paper_filed",
  "unknown",
  "rejected",
  "void",
];
/** Statuses an amendment can start from (store.py `start_amendment`). */
export const AMENDABLE: readonly ReturnStatus[] = ["accepted", "paper_filed", "transmitted"];

export type Editability =
  | { mode: "editable" }
  /** Editing is possible but consequential: saving reopens the return (review, approval, signature void). */
  | { mode: "bound"; reason: string; consequence: string }
  | { mode: "frozen"; reason: string; path: string };

export function describeStatus(status: ReturnStatus): string {
  switch (status) {
    case "preparing":
      return "In preparation";
    case "in_review":
      return "Submitted for review";
    case "approved":
      return "Approved by the reviewer";
    case "awaiting_signature":
      return "Awaiting the taxpayer's signature (Form 8879)";
    case "signed":
      return "Signed by the taxpayer";
    case "release_approved":
      return "Release approved: the workflow transmits it";
    case "transmitted":
      return "Transmitted, awaiting acknowledgement";
    case "accepted":
      return "Accepted";
    case "rejected":
      return "Rejected by the IRS";
    case "paper_filed":
      return "Filed on paper";
    case "unknown":
      return "Transmission outcome unknown";
    case "void":
      return "Void";
    default:
      return status.replaceAll("_", " ");
  }
}

export function editability(status: ReturnStatus): Editability {
  if (status === "preparing") return { mode: "editable" };
  if (status === "in_review") {
    return {
      mode: "bound",
      reason: "This return is submitted for review: its package is bound to the hash the reviewer sees.",
      consequence: "Saving a change sends it back to preparation; the reviewer sees it again once it is resubmitted.",
    };
  }
  if (HASH_BOUND.includes(status)) {
    return {
      mode: "bound",
      reason: `This return is ${describeStatus(status).toLowerCase()}: its package is bound to the hash the reviewer approved.`,
      consequence:
        "Saving a change reopens it: the approval, the signature request and any signature become void, queued " +
        "submissions are cancelled, and the return goes back to preparation.",
    };
  }
  switch (status) {
    case "unknown":
      return {
        mode: "frozen",
        reason: "An electronic transmission of this return was started and its outcome is not known.",
        path: "Reconcile it with the transmitter first (on the status page); nothing is edited or resent until then.",
      };
    case "rejected":
      return {
        mode: "frozen",
        reason: "This return was transmitted and rejected: what was sent is kept as it was.",
        path: "Correcting a rejected return needs the workflow's correct action, which the API does not expose yet (docs/WEB.md section 4); a reviewer can void it.",
      };
    case "void":
      return {
        mode: "frozen",
        reason: "This return is void and will not be filed through AgentLedger.",
        path: "Nothing changes here; a new return or an amendment starts afresh.",
      };
    default:
      return {
        mode: "frozen",
        reason: `This return is ${describeStatus(status).toLowerCase()}: it is evidence of what was filed and is never edited or recomputed in place.`,
        path: "A what-if goes through the recalculation preview; a change starts an amendment (Form 1040-X), both on the status page.",
      };
  }
}

/** Who submitted the return for review (the actor of the last submit event); the approver cannot be that person. */
export function submittedBy(history: WorkflowEvent[]): string | null {
  for (let i = history.length - 1; i >= 0; i--) {
    const event = history[i];
    if (event?.event === "submit_for_review") return event.actor;
  }
  return null;
}

export function lastEvent(history: WorkflowEvent[]): WorkflowEvent | null {
  return history[history.length - 1] ?? null;
}

/** The note of the last event of a kind (the void reason, how a paper return was filed). */
export function noteOf(history: WorkflowEvent[], event: ReturnEvent): string | null {
  for (let i = history.length - 1; i >= 0; i--) {
    const e = history[i];
    if (e?.event === event) return e.note || null;
  }
  return null;
}

/** store.py `relied_on`: every document the return relies on, plus those a preparer's edit replaced. */
export function reliedOn(inputs: Record<string, unknown>, provenance: Record<string, Provenance>): Set<string> {
  const out = new Set<string>();
  for (const p of Object.values(provenance)) {
    if (p.document_id) out.add(p.document_id);
    if (p.previous_document) out.add(p.previous_document);
    for (const d of p.documents ?? []) if (d.document_id) out.add(d.document_id);
  }
  for (const items of Object.values(inputs)) {
    if (!Array.isArray(items)) continue;
    for (const item of items) {
      const id = itemIdentity(item);
      if (id) out.add(id);
    }
  }
  return out;
}

/**
 * store.py `unaccounted_documents`: filed tax forms of the year (and the prior year's filed return) the return
 * neither uses nor has accounted for. `dispositions` are the ones this session knows (the API has no GET for them).
 */
export function unaccountedDocuments(
  detail: ReturnDetail,
  documents: ClientDocument[],
  dispositions: Disposition[],
): ClientDocument[] {
  const used = reliedOn(detail.inputs ?? {}, detail.provenance ?? {});
  for (const d of dispositions) used.add(d.document_id);
  const year = detail.return.tax_year;
  return documents.filter(
    (d) =>
      d.status === "filed" &&
      typeof d.doc_type === "string" &&
      TAX_FORMS.includes(d.doc_type) &&
      (d.tax_year === year || d.tax_year === null || (d.tax_year === year - 1 && d.doc_type === PRIOR_YEAR_RETURN)) &&
      !used.has(d.id),
  );
}

export interface Blocker {
  /** A stable code: the engine's diagnostic code, or one of the checklist's own. */
  code: string;
  text: string;
  count: number;
  /** Where to act: the review screen (inputs), the documents, or the status page itself. */
  where: "review" | "documents" | "status";
}

/**
 * store.py `_blockers`, as far as the API lets the app see it: computed, the engine's blocking diagnostics and the
 * coverage below preparation, missing amounts (open `missing:` conflicts and the never-zero rule over the inputs),
 * unconfirmed document amounts, orphaned items, open fact conflicts, unaccounted documents, and a cross-check that
 * disagrees. The API re-checks all of this on every transition and its 409 text is shown when it refuses.
 */
export function blockers(detail: ReturnDetail, conflicts: FactConflict[], unaccounted: ClientDocument[]): Blocker[] {
  const out: Blocker[] = [];
  const result = detail.result ?? null;
  if (!result) out.push({ code: "not_computed", text: "compute the return first", count: 1, where: "status" });
  for (const d of result?.diagnostics ?? []) {
    if (d.severity !== "error") continue;
    const where = d.form ? ` (${d.form}${d.line ? ` line ${d.line}` : ""})` : "";
    out.push({ code: d.code, text: `${d.message}${where}`, count: 1, where: "review" });
  }
  for (const b of result?.coverage?.below_preparation ?? []) {
    out.push({
      code: `coverage:${b.form}`,
      text: `${b.form} is ${b.status}; preparation needs ${b.need}${b.limits?.length ? ` (${b.limits.join("; ")})` : ""}`,
      count: 1,
      where: "status",
    });
  }
  const inputs = detail.inputs ?? {};
  const missingAnchors = new Set(missingAmounts(inputs).map((m) => m.anchor));
  for (const c of conflicts) if (c.anchor.startsWith("missing:")) missingAnchors.add(c.anchor);
  if (missingAnchors.size) {
    out.push({
      code: "missing_amount",
      text:
        `${missingAnchors.size} item(s) lack a required amount (for example a W-2 without wages): enter it from the ` +
        "document; a missing amount is never taken as zero",
      count: missingAnchors.size,
      where: "review",
    });
  }
  const unconfirmed = Object.values(detail.provenance ?? {}).filter((p) => !p.confirmed).length;
  if (unconfirmed) {
    out.push({
      code: "unconfirmed",
      text: `${unconfirmed} document-sourced amount(s) are not confirmed by the preparer`,
      count: unconfirmed,
      where: "review",
    });
  }
  const orphaned = conflicts.filter((c) => c.anchor.startsWith("orphan:")).length;
  if (orphaned) {
    out.push({
      code: "orphaned_item",
      text: `${orphaned} item(s) whose document left the return (moved, re-dated or deleted): remove each one or keep it with a reason`,
      count: orphaned,
      where: "review",
    });
  }
  const disagreements = conflicts.filter(
    (c) => !c.anchor.startsWith("orphan:") && !c.anchor.startsWith("missing:"),
  ).length;
  if (disagreements) {
    out.push({
      code: "fact_conflict",
      text: `${disagreements} fact conflict(s) between documents and the return must be resolved`,
      count: disagreements,
      where: "review",
    });
  }
  if (unaccounted.length) {
    const names = unaccounted
      .slice(0, 5)
      .map((d) => d.id)
      .join(", ");
    out.push({
      code: "unaccounted_document",
      text:
        `${unaccounted.length} filed document(s) for the year are not on the return (${names}): populate, or account for ` +
        "each one (entered by hand, or not applicable) with a reason",
      count: unaccounted.length,
      where: "documents",
    });
  }
  if (detail.crosscheck?.status === "differ") {
    out.push({
      code: "crosscheck_differs",
      text: "the independent cross-check disagrees; explain each difference when submitting for review",
      count: detail.crosscheck.discrepancies?.length ?? 1,
      where: "status",
    });
  }
  return out;
}

/** The API's 409 text: the guard's reasons joined by "; " (workflow/engine.py `send`), one per line. */
export function splitReasons(detail: string | undefined): string[] {
  if (!detail) return [];
  return detail
    .split(/;\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export type ActionId =
  "submit" | "request-changes" | "approve" | "request-signature" | "release-approve" | "void" | "amend" | "reconcile";

export interface ActionSpec {
  id: ActionId;
  label: string;
  /** The workflow events that make it available (any of them in `allowed`); amend goes by status instead. */
  events: ReturnEvent[];
  /** app.py `reviewer_only`. */
  reviewer: boolean;
  /** app.py `fresh`: a recent sign-in, so the step-up dialog may appear. */
  stepUp: boolean;
  tone: "primary" | "default" | "danger";
}

export const ACTIONS: readonly ActionSpec[] = [
  {
    id: "submit",
    label: "Submit for review",
    events: ["submit_for_review"],
    reviewer: false,
    stepUp: false,
    tone: "primary",
  },
  { id: "approve", label: "Approve", events: ["approve"], reviewer: true, stepUp: true, tone: "primary" },
  {
    id: "request-changes",
    label: "Request changes",
    events: ["request_changes"],
    reviewer: true,
    stepUp: false,
    tone: "default",
  },
  {
    id: "request-signature",
    label: "Request signature",
    events: ["request_signature"],
    reviewer: true,
    stepUp: true,
    tone: "primary",
  },
  {
    id: "release-approve",
    label: "Approve release for filing",
    events: ["approve_release"],
    reviewer: true,
    stepUp: true,
    tone: "primary",
  },
  {
    id: "reconcile",
    label: "Reconcile the transmission",
    events: ["reconciled_submitted", "reconciled_not_submitted"],
    reviewer: true,
    stepUp: true,
    tone: "primary",
  },
  { id: "amend", label: "Start an amendment", events: [], reviewer: true, stepUp: true, tone: "default" },
  { id: "void", label: "Void", events: ["void"], reviewer: true, stepUp: true, tone: "danger" },
];

/** The actions this status offers (store.py RETURN_1040 and `start_amendment`), in display order. */
export function availableActions(detail: ReturnDetail): ActionSpec[] {
  return ACTIONS.filter((a) =>
    a.id === "amend" ? AMENDABLE.includes(detail.status) : a.events.some((e) => detail.allowed.includes(e)),
  );
}

/** Whether the inputs editor may change anything right now (preparation steps are refused while frozen). */
export function isFrozen(status: ReturnStatus): boolean {
  return FROZEN.includes(status);
}

export function isRecordValue(value: unknown): value is Record<string, unknown> {
  return isRecord(value);
}
