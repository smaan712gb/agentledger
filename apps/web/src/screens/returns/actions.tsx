import {
  isApiError,
  type AmendResult,
  type Me,
  type ReturnActionBody,
  type ReturnActionResult,
  type ReturnDetail,
  type VoidResult,
} from "@agentledger/contracts";
import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useId, useState, type ReactNode, type SyntheticEvent } from "react";

import { api } from "../../api";
import { can, type Decision } from "../../auth/can";
import { Button } from "../../ui/Button";
import { Dialog } from "../../ui/Dialog";
import { FormError, InputField, SelectField } from "../../ui/Field";
import { useToast } from "../../ui/Toast";
import screens from "../screens.module.css";
import styles from "./returns.module.css";
import { availableActions, describeStatus, submittedBy, type ActionId, type ActionSpec } from "./returnState";

/** The actions that run at once; the others ask for something first (a note, a reason, the jurisdictions). */
const DIRECT: readonly ActionId[] = ["approve", "request-signature"];

type Variables = { id: ActionId; body: ReturnActionBody & { note?: string } };
type Outcome = ReturnActionResult | VoidResult | AmendResult;

/**
 * The workflow actions a status offers (store.py RETURN_1040), disabled with the API's reason when this person may not
 * take them: reviewer actions need a credentialed reviewer, approval needs someone other than the submitter, and the
 * consequential ones need a recent sign-in (the fetch layer opens the step-up dialog and retries once). A 409 is the
 * workflow's refusal and goes to `onRefused`, which the status page renders as the Conflict state with each reason.
 */
export function ActionBar({
  detail,
  me,
  onChanged,
  onRefused,
}: {
  detail: ReturnDetail;
  me: Me;
  onChanged: () => Promise<unknown>;
  onRefused: (text: string) => void;
}) {
  const navigate = useNavigate();
  const { toast } = useToast();
  const [open, setOpen] = useState<ActionId | null>(null);
  const noteId = useId();
  const rid = detail.return.id;
  const clientId = detail.return.client_id;
  const submitter = submittedBy(detail.history);
  const actions = availableActions(detail);

  const run = useMutation({
    mutationFn: ({ id, body }: Variables): Promise<Outcome> => {
      if (id === "void") return api.returns.void(rid, body.note ?? "");
      if (id === "amend") return api.returns.amend(rid);
      return api.returns.action(rid, id, body);
    },
    onSuccess: async (result, { id }) => {
      setOpen(null);
      if (id === "amend") {
        const amendment = result as AmendResult;
        toast("Amendment started: a Form 1040-X carrying the filed return's facts.", "success");
        await navigate({ to: "/returns/$rid", params: { rid: amendment.id } });
        return;
      }
      const status = (result as ReturnActionResult | VoidResult).status;
      toast(`Done. The return is now ${describeStatus(status).toLowerCase()}.`, "success");
      await onChanged();
    },
    onError: (err) => {
      if (isApiError(err) && err.status === 409) {
        setOpen(null);
        onRefused(err.message);
        return;
      }
      toast(isApiError(err) ? err.message : "The action could not be completed.", "error");
    },
  });

  function decisionFor(a: ActionSpec): Decision {
    if (!a.reviewer) return can(me, "returns.prepare", { clientId });
    return can(me, "returns.review", { clientId, submittedBy: a.id === "approve" ? submitter : null });
  }

  if (!actions.length) {
    return (
      <p className={screens.hint}>
        No action is open in this status{detail.waiting_on ? `: waiting on ${detail.waiting_on}` : ""}.
      </p>
    );
  }
  const needsStepUp = actions.some((a) => a.stepUp);
  return (
    <>
      <div className={screens.actions} data-testid="return-actions">
        {actions.map((a) => {
          const d = decisionFor(a);
          return (
            <Button
              key={a.id}
              tone={a.tone}
              disabledReason={d.allowed ? undefined : d.reason}
              busy={run.isPending && run.variables.id === a.id}
              aria-describedby={a.stepUp ? noteId : undefined}
              onClick={() => {
                if (DIRECT.includes(a.id)) run.mutate({ id: a.id, body: {} });
                else setOpen(a.id);
              }}
            >
              {a.label}
            </Button>
          );
        })}
      </div>
      {needsStepUp ? (
        <p id={noteId} className={screens.hint}>
          Approving, requesting a signature, releasing, voiding or amending needs a recent sign-in: you may be asked for
          your code.
        </p>
      ) : null}
      <ActionDialog
        open={open}
        detail={detail}
        busy={run.isPending}
        onCancel={() => setOpen(null)}
        onConfirm={(body) => {
          if (open) run.mutate({ id: open, body });
        }}
      />
    </>
  );
}

function ActionDialog({
  open,
  detail,
  busy,
  onCancel,
  onConfirm,
}: {
  open: ActionId | null;
  detail: ReturnDetail;
  busy: boolean;
  onCancel: () => void;
  onConfirm: (body: ReturnActionBody & { note?: string }) => void;
}) {
  const [text, setText] = useState("");
  const [jurisdictions, setJurisdictions] = useState("US-FED");
  const [submitted, setSubmitted] = useState<"yes" | "no">("no");
  const [submissionId, setSubmissionId] = useState("");
  const [evidence, setEvidence] = useState("");
  const [submission, setSubmission] = useState("");
  const [error, setError] = useState<string | null>(null);
  const unknownSubmissions = detail.filing.submissions.filter((s) => s.status === "unknown");

  function close() {
    setText("");
    setError(null);
    onCancel();
  }

  function submit(e: SyntheticEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    switch (open) {
      case "submit":
        if (detail.crosscheck?.status === "differ" && !text.trim()) {
          setError("The cross-check disagrees: explain each difference before submitting.");
          return;
        }
        onConfirm(text.trim() ? { explanation: text.trim() } : {});
        return;
      case "request-changes":
        onConfirm({ note: text.trim() });
        return;
      case "void":
        if (text.trim().length < 10) {
          setError(
            "Say why the return is void, in at least ten characters (for example 'filed with other software on 2027-04-10, transcript on file').",
          );
          return;
        }
        onConfirm({ note: text.trim() });
        return;
      case "release-approve":
        onConfirm({ jurisdictions: jurisdictions.split(/[\s,]+/).filter(Boolean) });
        return;
      case "reconcile": {
        if (submitted === "yes" && !submissionId.trim()) {
          setError("Enter the transmitter's submission id so the acknowledgement can be matched.");
          return;
        }
        if (!evidence.trim()) {
          setError("Record what the transmitter said (the evidence of the reconciliation).");
          return;
        }
        const body: ReturnActionBody = { submitted: submitted === "yes", evidence: evidence.trim() };
        if (submissionId.trim()) body.submission_id = submissionId.trim();
        if (submission) body.submission = submission;
        onConfirm(body);
        return;
      }
      case "amend":
        onConfirm({});
        return;
      default:
        return;
    }
  }

  const titles: Record<ActionId, string> = {
    submit: "Submit for review",
    approve: "Approve",
    "request-changes": "Request changes",
    "request-signature": "Request signature",
    "release-approve": "Approve the release for filing",
    void: "Void this return",
    amend: "Start an amendment",
    reconcile: "Reconcile the transmission",
  };
  const descriptions: Record<ActionId, string> = {
    submit: "The reviewer sees the return as it is now; every blocker is checked first.",
    approve: "",
    "request-changes": "The return goes back to preparation with your note.",
    "request-signature": "",
    "release-approve":
      "The signed package goes to the named jurisdictions and the workflow transmits it. Every filing check runs now.",
    void: "Irreversible. The return will not be filed through AgentLedger; its documents stop waiting for it.",
    amend: "A linked Form 1040-X is created from the filed return's facts; the filed return stays as filed.",
    reconcile:
      "A transmission whose outcome is unknown is never resent blindly: record what the transmitter says happened.",
  };

  let body: ReactNode = null;
  switch (open) {
    case "submit":
      body = (
        <TextArea
          label="Explanation for the reviewer"
          hint={
            detail.crosscheck?.status === "differ"
              ? "Required: the independent cross-check disagrees; explain each difference."
              : "Optional."
          }
          value={text}
          onChange={setText}
        />
      );
      break;
    case "request-changes":
      body = <TextArea label="What should change" value={text} onChange={setText} />;
      break;
    case "void":
      body = (
        <TextArea
          label="Why the return is void"
          hint="At least ten characters; on record."
          value={text}
          onChange={setText}
        />
      );
      break;
    case "release-approve":
      body = (
        <InputField
          label="Jurisdictions"
          hint="US-FED, and states as US-XX, separated by commas."
          value={jurisdictions}
          onChange={(e) => setJurisdictions(e.target.value)}
        />
      );
      break;
    case "reconcile":
      body = (
        <>
          {unknownSubmissions.length ? (
            <SelectField label="Submission" value={submission} onChange={(e) => setSubmission(e.target.value)}>
              <option value="">The return's federal transmission</option>
              {unknownSubmissions.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.jurisdiction} · {s.id}
                </option>
              ))}
            </SelectField>
          ) : null}
          <SelectField
            label="Did the transmitter receive it?"
            value={submitted}
            onChange={(e) => setSubmitted(e.target.value === "yes" ? "yes" : "no")}
          >
            <option value="no">No: it was never received; the return goes back to signed</option>
            <option value="yes">Yes: it was received; the return is transmitted</option>
          </SelectField>
          <InputField
            label="Transmitter's submission id"
            hint="Required when it was received."
            value={submissionId}
            onChange={(e) => setSubmissionId(e.target.value)}
          />
          <TextArea
            label="Evidence"
            hint="What the transmitter said, and when."
            value={evidence}
            onChange={setEvidence}
          />
        </>
      );
      break;
    default:
      body = null;
  }

  return (
    <Dialog
      open={open !== null}
      onOpenChange={(isOpen) => {
        if (!isOpen) close();
      }}
      title={open ? titles[open] : ""}
      description={open && descriptions[open] ? descriptions[open] : undefined}
    >
      <form onSubmit={submit} aria-label={open ? titles[open] : undefined}>
        <FormError message={error} />
        {body}
        <div className={screens.actions}>
          <Button type="button" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" tone={open === "void" ? "danger" : "primary"} busy={busy}>
            {open ? titles[open] : "Confirm"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function TextArea({
  label,
  hint,
  value,
  onChange,
}: {
  label: string;
  hint?: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const id = useId();
  return (
    <div className={styles.field}>
      <label htmlFor={id} className={styles.label}>
        {label}
      </label>
      <textarea
        id={id}
        className={[styles.control, styles.textarea].join(" ")}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-describedby={hint ? `${id}-hint` : undefined}
      />
      {hint ? (
        <div id={`${id}-hint`} className={screens.meta}>
          {hint}
        </div>
      ) : null}
    </div>
  );
}
