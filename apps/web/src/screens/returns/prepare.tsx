import { isApiError, type PopulateResult, type ReturnDetail } from "@agentledger/contracts";
import { useMutation } from "@tanstack/react-query";
import { useId, useState } from "react";

import { api } from "../../api";
import { can } from "../../auth/can";
import { Button } from "../../ui/Button";
import { PartialSuccess, type PartialItem } from "../../ui/states";
import { useToast } from "../../ui/Toast";
import screens from "../screens.module.css";
import { editability, isFrozen } from "./returnState";
import type { ReturnWorkspace } from "./useReturnWorkspace";

/** One line per document populated and per issue populate reported (never guessed: a person decides each one). */
export function describePopulate(result: PopulateResult): PartialItem[] {
  const items: PartialItem[] = result.documents.map((id) => ({
    key: `doc:${id}`,
    label: id,
    outcome: "ok",
    detail: "read onto the return with its provenance",
  }));
  for (const [i, issue] of result.issues.entries()) {
    items.push({
      key: `issue:${i}:${issue.code}`,
      label: issue.document_id ?? issue.code,
      outcome: "warn",
      detail: `${issue.code}: ${issue.message}`,
    });
  }
  if (!items.length) {
    items.push({
      key: "none",
      label: "No documents",
      outcome: "warn",
      detail: "no filed document of the year feeds this return",
    });
  }
  return items;
}

/**
 * The preparation steps (app.py `cpa_only`): populate from documents, confirm the document-sourced amounts, compute
 * (optionally with the independent cross-check). Refused on a frozen return with the engine's reason; a 409 (the
 * workflow's refusal) goes to `onRefused`.
 */
export function PrepareActions({
  ws,
  detail,
  onRefused,
}: {
  ws: ReturnWorkspace;
  detail: ReturnDetail;
  onRefused: (text: string) => void;
}) {
  const { toast } = useToast();
  const checkId = useId();
  const [crosscheck, setCrosscheck] = useState(false);
  const [populated, setPopulated] = useState<PartialItem[] | null>(null);
  const clientId = detail.return.client_id;
  const decision = can(ws.me, "returns.prepare", { clientId });
  const frozen = isFrozen(detail.status);
  const edit = editability(detail.status);
  const reason = !decision.allowed ? decision.reason : frozen && edit.mode === "frozen" ? edit.reason : undefined;
  const unconfirmed = Object.values(detail.provenance ?? {}).filter((p) => !p.confirmed).length;

  const onError = (err: unknown) => {
    if (isApiError(err) && err.status === 409) onRefused(err.message);
    else toast(isApiError(err) ? err.message : "The step could not be completed.", "error");
  };
  const populate = useMutation({
    mutationFn: () => api.returns.populate(ws.rid),
    onSuccess: async (result) => {
      setPopulated(describePopulate(result));
      toast(
        `Populated from ${result.documents.length} document(s): ${result.fields} field(s) changed, ${result.conflicts} open conflict(s).`,
        result.conflicts ? "info" : "success",
      );
      await ws.invalidate();
    },
    onError,
  });
  const confirm = useMutation({
    mutationFn: () => api.returns.confirm(ws.rid),
    onSuccess: async (r) => {
      toast(`${r.confirmed} document amount(s) confirmed.`, "success");
      await ws.invalidate();
    },
    onError,
  });
  const compute = useMutation({
    mutationFn: () => api.returns.compute(ws.rid, crosscheck),
    onSuccess: async (r) => {
      const errors = r.diagnostics.filter((d) => d.severity === "error").length;
      toast(errors ? `Computed with ${errors} blocking diagnostic(s).` : "Computed.", errors ? "info" : "success");
      await ws.invalidate();
    },
    onError,
  });

  return (
    <>
      <div className={screens.actions}>
        <Button onClick={() => populate.mutate()} busy={populate.isPending} disabledReason={reason}>
          Populate from documents
        </Button>
        <Button
          onClick={() => confirm.mutate()}
          busy={confirm.isPending}
          disabledReason={reason ?? (unconfirmed === 0 ? "every document-sourced amount is confirmed" : undefined)}
        >
          Confirm document amounts ({unconfirmed})
        </Button>
        <Button onClick={() => compute.mutate()} busy={compute.isPending} disabledReason={reason} tone="primary">
          Compute
        </Button>
        <span className={screens.inlineForm}>
          <input
            id={checkId}
            type="checkbox"
            checked={crosscheck}
            disabled={Boolean(reason)}
            onChange={(e) => setCrosscheck(e.target.checked)}
          />
          <label htmlFor={checkId} className="small">
            with the independent cross-check
          </label>
        </span>
      </div>
      {populated ? <PartialSuccess items={populated} onDismiss={() => setPopulated(null)} /> : null}
    </>
  );
}
