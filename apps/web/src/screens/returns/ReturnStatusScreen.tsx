import type { FilingSubmission, ReturnDetail } from "@agentledger/contracts";
import { isApiError } from "@agentledger/contracts";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { useState } from "react";

import { api } from "../../api";
import { can } from "../../auth/can";
import { formatDateTime, formatMoney, humanise } from "../../lib/format";
import { queries } from "../../queries";
import { Button } from "../../ui/Button";
import { Card, Stat } from "../../ui/Card";
import { Chip, StatusChip } from "../../ui/Chip";
import { Dialog } from "../../ui/Dialog";
import { Empty, Frozen, Loading, STATE_NAMES, StatePanel } from "../../ui/states";
import { useToast } from "../../ui/Toast";
import screens from "../screens.module.css";
import { ActionBar } from "./actions";
import { PrepareActions } from "./prepare";
import { editability, noteOf, splitReasons } from "./returnState";
import styles from "./returns.module.css";
import { useReturnWorkspace } from "./useReturnWorkspace";

export function ReturnStatusScreen() {
  const { rid } = useParams({ from: "/_app/returns/$rid/" });
  const ws = useReturnWorkspace(rid);
  const [refused, setRefused] = useState<string | null>(null);
  const [preview, setPreview] = useState(false);
  const detail = ws.detail.data;
  if (!detail) return null; // the layout's boundary renders the loading and failure states
  const edit = editability(detail.status);
  const result = detail.result ?? null;
  const summary = result?.summary ?? detail.summary;
  const outcome =
    summary.refund && summary.refund !== "0"
      ? { label: "Refund", value: formatMoney(summary.refund) }
      : { label: "Amount owed", value: formatMoney(summary.amount_owed) };

  return (
    <>
      {edit.mode === "frozen" ? (
        <Frozen reason={`${edit.reason} ${edit.path}`} compact={false} />
      ) : edit.mode === "bound" ? (
        <Frozen reason={`${edit.reason} ${edit.consequence}`} />
      ) : null}
      {refused ? (
        <StatePanel
          title={STATE_NAMES.conflict}
          tone="warn"
          role="alert"
          state="conflict"
          actions={
            <Button
              onClick={() => {
                setRefused(null);
                void ws.invalidate();
              }}
            >
              Refresh
            </Button>
          }
        >
          <p>The workflow refused the step:</p>
          <ul className={styles.reasons}>
            {splitReasons(refused).map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </StatePanel>
      ) : null}

      <Card title="Actions">
        <ActionBar detail={detail} me={ws.me} onChanged={ws.invalidate} onRefused={setRefused} />
        {detail.inputs !== undefined ? <PrepareActions ws={ws} detail={detail} onRefused={setRefused} /> : null}
      </Card>

      <div className={screens.grid}>
        {result ? (
          <>
            <Stat label="Adjusted gross income" value={formatMoney(summary.agi)} hint="Form 1040 line 11" />
            <Stat label="Taxable income" value={formatMoney(summary.taxable_income)} hint="line 15" />
            <Stat label="Total tax" value={formatMoney(summary.total_tax)} hint="line 24" />
            <Stat label="Payments" value={formatMoney(summary.payments)} hint="line 33" />
            <Stat
              label={outcome.label}
              value={outcome.value}
              hint={result.pinned ? `engine ${result.pinned.engine} · rules ${result.pinned.kb_version}` : undefined}
            />
          </>
        ) : (
          <Empty title="Not computed yet">
            <p>Compute the return to see its figures; nothing is computed until a person asks.</p>
          </Empty>
        )}
      </div>

      <div className={screens.twoCol}>
        <Card title={ws.checklist.length ? `Before it moves on (${ws.checklist.length})` : "Before it moves on"}>
          {ws.checklist.length ? (
            <ul className={styles.checklist} aria-label="Blocking checklist">
              {ws.checklist.map((b) => (
                <li key={b.code} className={styles.checklistItem}>
                  <code className={styles.code}>{b.code}</code>
                  <span>
                    {b.text}
                    {b.where === "review" ? (
                      <>
                        {" "}
                        <Link to="/returns/$rid/review" params={{ rid }}>
                          Open the inputs
                        </Link>
                      </>
                    ) : b.where === "documents" ? (
                      <>
                        {" "}
                        <Link to="/returns/$rid/review" params={{ rid }} hash="documents">
                          Account for the documents
                        </Link>
                      </>
                    ) : null}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className={screens.hint}>
              Nothing blocks the next step as far as the app can see; the workflow re-checks everything when a step is
              taken.
            </p>
          )}
          {ws.moreDocuments ? (
            <p className={screens.hint}>Only the client's 50 most recent documents were checked for the year.</p>
          ) : null}
        </Card>

        <Card title="Cross-check">
          {detail.crosscheck ? (
            <>
              <p>
                <StatusChip status={detail.crosscheck.status} />{" "}
                {detail.crosscheck.status === "agree"
                  ? `The independent oracle agrees on ${detail.crosscheck.compared ?? 0} compared item(s).`
                  : detail.crosscheck.status === "differ"
                    ? "The independent oracle disagrees; each difference needs an explanation before review."
                    : "The oracle is not installed on this deployment; the return was computed without it."}
              </p>
              {detail.crosscheck.discrepancies?.length ? (
                <table>
                  <caption className="visually-hidden">Cross-check discrepancies</caption>
                  <thead>
                    <tr>
                      <th scope="col">Item</th>
                      <th scope="col">AgentLedger</th>
                      <th scope="col">PolicyEngine</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.crosscheck.discrepancies.map((d) => (
                      <tr key={d.item}>
                        <td>{d.item}</td>
                        <td className="num">{formatMoney(d.agentledger)}</td>
                        <td className="num">{formatMoney(d.policyengine)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : null}
              {detail.crosscheck.unmodelled?.length ? (
                <p className={screens.meta}>Not modelled by the oracle: {detail.crosscheck.unmodelled.join(", ")}</p>
              ) : null}
            </>
          ) : (
            <p className={screens.hint}>Not run. Compute with the independent cross-check to compare the figures.</p>
          )}
        </Card>

        <FilingCard detail={detail} onRefused={setRefused} onChanged={ws.invalidate} />

        <Card title="Context">
          <dl className={screens.dl}>
            <dt>Created</dt>
            <dd>
              {formatDateTime(detail.return.created_at)} by {detail.return.created_by}
            </dd>
            <dt>Amends</dt>
            <dd>
              {detail.return.amends ? (
                <Link to="/returns/$rid" params={{ rid: detail.return.amends }}>
                  {detail.return.amends}
                </Link>
              ) : (
                "—"
              )}
            </dd>
            {detail.status === "void" ? (
              <>
                <dt>Void because</dt>
                <dd>{noteOf(detail.history, "void") ?? "no reason recorded"}</dd>
              </>
            ) : null}
            {detail.status === "paper_filed" ? (
              <>
                <dt>Filed on paper</dt>
                <dd>{noteOf(detail.history, "mark_paper_filed") ?? "no note recorded"}</dd>
              </>
            ) : null}
            {detail.status === "unknown" ? (
              <>
                <dt>Transmission</dt>
                <dd>Outcome unknown: reconcile it with the transmitter (Actions) before anything else happens.</dd>
              </>
            ) : null}
          </dl>
          {edit.mode === "frozen" && detail.status !== "void" && detail.status !== "unknown" ? (
            <div className={screens.actions} style={{ marginTop: 12 }}>
              <Button onClick={() => setPreview(true)}>What-if under today's rules</Button>
              <span className={screens.meta}>A recalculation preview; nothing is stored on a filed return.</span>
            </div>
          ) : null}
        </Card>

        <Card title="History">
          <ol className={styles.timeline} aria-label="Workflow history">
            {[...detail.history].reverse().map((h) => (
              <li key={h.seq} className={styles.timelineItem}>
                <span className={screens.meta}>{formatDateTime(h.at)}</span>
                <span>
                  <strong>{humanise(h.event)}</strong> by {h.actor} <StatusChip status={h.status} />
                  {h.note ? <div className={screens.meta}>{h.note}</div> : null}
                </span>
              </li>
            ))}
          </ol>
        </Card>
      </div>
      <RecalculationDialog rid={rid} open={preview} onClose={() => setPreview(false)} />
    </>
  );
}

function FilingCard({
  detail,
  onRefused,
  onChanged,
}: {
  detail: ReturnDetail;
  onRefused: (text: string) => void;
  onChanged: () => Promise<unknown>;
}) {
  const ws = useReturnWorkspace(detail.return.id);
  const { toast } = useToast();
  const filing = detail.filing;
  const reviewer = can(ws.me, "returns.review", { clientId: detail.return.client_id });
  const retransmit = useMutation({
    mutationFn: (s: FilingSubmission) => api.returns.action(detail.return.id, "retransmit", { submission_id: s.id }),
    onSuccess: async () => {
      toast("Retransmission queued.", "success");
      await onChanged();
    },
    onError: (err) => {
      if (isApiError(err) && err.status === 409) onRefused(err.message);
      else toast(isApiError(err) ? err.message : "The retransmission could not be queued.", "error");
    },
  });
  return (
    <Card title="Filing">
      {filing.release ? (
        <p>
          Release approved by {filing.release.approved_by ?? "—"} on {formatDateTime(filing.release.at)} for{" "}
          {filing.release.jurisdictions.join(", ")}.{" "}
          {filing.complete ? <Chip tone="good">complete</Chip> : <Chip tone="info">in progress</Chip>}
        </p>
      ) : (
        <p className={screens.hint}>
          {detail.status === "paper_filed"
            ? "Filed on paper; no electronic submission."
            : "Not released for filing yet. The release is a CPA's decision after the taxpayer has signed."}
        </p>
      )}
      {filing.submissions.length ? (
        <table>
          <caption className="visually-hidden">Submissions by jurisdiction</caption>
          <thead>
            <tr>
              <th scope="col">Jurisdiction</th>
              <th scope="col">Kind</th>
              <th scope="col">Status</th>
              <th scope="col">Submission id</th>
              <th scope="col">
                <span className="visually-hidden">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {filing.submissions.map((s) => (
              <tr key={s.id}>
                <td>{s.jurisdiction}</td>
                <td>
                  {s.kind}
                  {s.attempt > 1 ? ` (attempt ${s.attempt})` : ""}
                </td>
                <td>
                  <StatusChip status={s.status} />
                  {s.rejection_codes.length ? <div className={screens.meta}>{s.rejection_codes.join(", ")}</div> : null}
                </td>
                <td>
                  <code>{s.provider_submission_id ?? s.planned_submission_id ?? s.id}</code>
                </td>
                <td>
                  {s.status === "rejected" && s.jurisdiction !== "US-FED" ? (
                    <Button
                      size="sm"
                      disabledReason={reviewer.allowed ? undefined : reviewer.reason}
                      busy={retransmit.isPending && retransmit.variables.id === s.id}
                      onClick={() => retransmit.mutate(s)}
                    >
                      Retransmit
                    </Button>
                  ) : s.status === "rejected" ? (
                    <span className={screens.meta}>correct the return, then review, sign and release again</span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </Card>
  );
}

function RecalculationDialog({ rid, open, onClose }: { rid: string; open: boolean; onClose: () => void }) {
  const preview = useQuery({ ...queries.recalculation(rid), enabled: open });
  return (
    <Dialog
      open={open}
      onOpenChange={(isOpen) => {
        if (!isOpen) onClose();
      }}
      title="What-if under today's rules"
      description="The stored inputs recomputed with the current rules and engine. Nothing is stored; the filed return stays as filed."
    >
      {preview.isPending ? <Loading label="Recomputing" /> : null}
      {preview.isError ? (
        <p role="alert">{isApiError(preview.error) ? preview.error.message : "The preview failed."}</p>
      ) : null}
      {preview.data ? (
        Object.keys(preview.data.changes_vs_latest).length ? (
          <table>
            <caption className="visually-hidden">Changes against the filed figures</caption>
            <thead>
              <tr>
                <th scope="col">Figure</th>
                <th scope="col">As filed</th>
                <th scope="col">Today</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(preview.data.changes_vs_latest).map(([k, v]) => (
                <tr key={k}>
                  <td>{humanise(k)}</td>
                  <td className="num">{formatMoney(v.latest)}</td>
                  <td className="num">{formatMoney(v.now)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p>Today's rules give the same figures.</p>
        )
      ) : null}
      <div className={screens.actions}>
        <Button onClick={onClose}>Close</Button>
      </div>
    </Dialog>
  );
}
