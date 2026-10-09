import { isApiError, type Client, type ReviewDocument } from "@agentledger/contracts";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";

import { api } from "../api";
import { useMe } from "../auth/AuthProvider";
import { can } from "../auth/can";
import { formatDateTime, formatPercent } from "../lib/format";
import { queries, queryKeys } from "../queries";
import { Card, PageHeader } from "../ui/Card";
import { Empty, PartialSuccess, QueryBoundary, type PartialItem } from "../ui/states";
import { useToast } from "../ui/Toast";
import { describeUpload } from "./DocumentsScreen";
import { DownloadButton } from "./DownloadButton";
import styles from "./screens.module.css";

export function InboxScreen() {
  const me = useMe();
  const qc = useQueryClient();
  const { toast } = useToast();
  const queue = useQuery(queries.reviewQueue());
  const clients = useQuery(queries.clients());
  const assign = can(me, "documents.assign");
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [busyDoc, setBusyDoc] = useState<string | null>(null);
  const [results, setResults] = useState<PartialItem[] | null>(null);

  async function file(doc: ReviewDocument, clientId: string) {
    if (!clientId) return;
    setBusyDoc(doc.id);
    try {
      await api.documents.assign(doc.id, clientId);
      const client = clients.data?.find((c) => c.id === clientId);
      toast(`${doc.original_name} filed to ${client?.name ?? clientId}.`, "success");
      await Promise.all([
        qc.invalidateQueries({ queryKey: queryKeys.reviewQueue }),
        qc.invalidateQueries({ queryKey: queryKeys.client(clientId) }),
      ]);
    } catch (err) {
      toast(isApiError(err) ? err.message : "Could not file the document.", "error");
    } finally {
      setBusyDoc(null);
    }
  }

  async function upload(files: FileList) {
    const items: PartialItem[] = [];
    for (const f of Array.from(files)) {
      try {
        items.push(...describeUpload(f.name, await api.documents.upload(f)));
      } catch (err) {
        items.push({
          key: f.name,
          label: f.name,
          outcome: "failed",
          detail: isApiError(err) ? err.message : "upload failed",
        });
      }
    }
    setResults(items);
    await Promise.all([
      qc.invalidateQueries({ queryKey: queryKeys.reviewQueue }),
      qc.invalidateQueries({ queryKey: queryKeys.clients }),
    ]);
    if (inputRef.current) inputRef.current.value = "";
  }

  return (
    <>
      <PageHeader
        title="Intake inbox"
        subtitle="Everything that arrived by email, maildrop, upload or connector and could not be filed with confidence. Nothing is guessed into a client."
      />
      <div className={styles.drop}>
        <label htmlFor={inputId}>
          <strong>Drop documents for any client</strong> — AgentLedger works out whose they are, or asks here.
        </label>
        <input
          id={inputId}
          ref={inputRef}
          type="file"
          multiple
          onChange={(e) => {
            if (e.target.files) void upload(e.target.files);
          }}
        />
      </div>
      {results ? <PartialSuccess items={results} onDismiss={() => setResults(null)} /> : null}
      <QueryBoundary
        query={queue}
        isEmpty={(docs) => docs.length === 0}
        empty={
          <Empty title="Inbox zero">
            <p>Everything was filed automatically.</p>
          </Empty>
        }
      >
        {(docs) => (
          <Card>
            <table>
              <caption className="visually-hidden">Documents waiting for review</caption>
              <thead>
                <tr>
                  <th scope="col">Document</th>
                  <th scope="col">Looks like</th>
                  <th scope="col">Why it is here</th>
                  <th scope="col">File to</th>
                  <th scope="col">
                    <span className="visually-hidden">Download</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {docs.map((d) => (
                  <tr key={d.id}>
                    <td>
                      <strong>{d.original_name}</strong>
                      <div className={styles.meta}>
                        {d.sender ?? d.channel} · {formatDateTime(d.received_at)}
                      </div>
                    </td>
                    <td>
                      {d.doc_type ?? "—"} {d.tax_year ?? ""}
                      {d.summary ? <div className={styles.meta}>{d.summary}</div> : null}
                    </td>
                    <td className="small">
                      {formatPercent(d.confidence)} confidence · {d.classified_by ?? "unclassified"}
                    </td>
                    <td>
                      <AssignSelect
                        doc={d}
                        clients={clients.data ?? []}
                        busy={busyDoc === d.id}
                        disabledReason={assign.allowed ? undefined : assign.reason}
                        onAssign={(cid) => void file(d, cid)}
                      />
                    </td>
                    <td>
                      <DownloadButton docId={d.id} filename={d.original_name} size="sm" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )}
      </QueryBoundary>
    </>
  );
}

function AssignSelect({
  doc,
  clients,
  busy,
  disabledReason,
  onAssign,
}: {
  doc: ReviewDocument;
  clients: Client[];
  busy: boolean;
  disabledReason: string | undefined;
  onAssign: (clientId: string) => void;
}) {
  const id = useId();
  return (
    <>
      <label htmlFor={id} className="visually-hidden">
        File {doc.original_name} to a client
      </label>
      <select
        id={id}
        className={styles.select}
        defaultValue=""
        disabled={busy || Boolean(disabledReason)}
        title={disabledReason}
        aria-busy={busy || undefined}
        onChange={(e) => onAssign(e.target.value)}
        data-testid={`assign-${doc.id}`}
      >
        <option value="">choose client…</option>
        {clients.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </select>
      {disabledReason ? <div className={styles.meta}>{disabledReason}</div> : null}
    </>
  );
}
