import { isApiError, type ClientDetail, type ClientDocument, type UploadResult } from "@agentledger/contracts";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { useId, useMemo, useRef, useState, type DragEvent } from "react";

import { api } from "../api";
import { useMe } from "../auth/AuthProvider";
import { can } from "../auth/can";
import { formatDateTime, formatPercent } from "../lib/format";
import { queryKeys } from "../queries";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { StatusChip } from "../ui/Chip";
import { DataTable } from "../ui/DataTable";
import { Empty, PartialSuccess, QueryBoundary, type PartialItem } from "../ui/states";
import { useToast } from "../ui/Toast";
import { useClientDetail, useInvalidateClient } from "./clientDetail";
import { DownloadButton } from "./DownloadButton";
import styles from "./screens.module.css";

/** One line per uploaded part: filed, not filed (review), already stored, or failed. */
export function describeUpload(file: string, results: UploadResult[]): PartialItem[] {
  if (!results.length) return [{ key: file, label: file, outcome: "warn", detail: "the file produced no documents" }];
  return results.map((r, i) => {
    const key = `${file}:${r.id}:${i}`;
    if (r.duplicate)
      return { key, label: r.name, outcome: "warn", detail: "already stored: these exact bytes were uploaded before" };
    if (r.status === "filed") {
      return {
        key,
        label: r.name,
        outcome: "ok",
        detail: `filed as ${r.doc_type}${r.tax_year ? ` ${r.tax_year}` : ""} (${formatPercent(r.confidence)} confidence)`,
      };
    }
    return {
      key,
      label: r.name,
      outcome: "warn",
      detail: `not filed: ${r.doc_type} at ${formatPercent(r.confidence)} confidence is waiting in the inbox for review`,
    };
  });
}

export function DocumentsScreen() {
  const { query } = useClientDetail();
  return <QueryBoundary query={query}>{(detail) => <Documents detail={detail} />}</QueryBoundary>;
}

function Documents({ detail }: { detail: ClientDetail }) {
  const me = useMe();
  const qc = useQueryClient();
  const invalidate = useInvalidateClient();
  const { toast } = useToast();
  const clientId = detail.client.id;
  const upload = can(me, "documents.upload", { clientId });
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [results, setResults] = useState<PartialItem[] | null>(null);

  async function uploadFiles(files: FileList | File[]) {
    const list = Array.from(files);
    if (!list.length) return;
    const items: PartialItem[] = [];
    for (const file of list) {
      setBusy(file.name);
      try {
        const out = await api.documents.upload(file, clientId);
        items.push(...describeUpload(file.name, out));
      } catch (err) {
        items.push({
          key: file.name,
          label: file.name,
          outcome: "failed",
          detail: isApiError(err) ? err.message : "upload failed",
        });
      }
    }
    setBusy(null);
    setResults(items);
    await Promise.all([invalidate(clientId), qc.invalidateQueries({ queryKey: queryKeys.reviewQueue })]);
    if (inputRef.current) inputRef.current.value = "";
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setOver(false);
    if (!upload.allowed) {
      toast(upload.reason ?? "not allowed", "error");
      return;
    }
    void uploadFiles(e.dataTransfer.files);
  }

  const columns = useMemo<ColumnDef<ClientDocument>[]>(
    () => [
      {
        id: "name",
        header: "Document",
        accessorFn: (d) => d.original_name,
        cell: ({ row }) => (
          <>
            <Link
              to="/clients/$clientId/documents/$docId"
              params={{ clientId, docId: row.original.id }}
              search={(prev) => prev}
            >
              {row.original.original_name}
            </Link>
            {row.original.summary ? <div className={styles.meta}>{row.original.summary}</div> : null}
          </>
        ),
      },
      { id: "type", header: "Type", accessorFn: (d) => d.doc_type ?? "—" },
      {
        id: "year",
        header: "Year",
        accessorFn: (d) => d.tax_year ?? "",
        cell: ({ row }) => row.original.tax_year ?? "—",
        meta: { align: "right" },
      },
      {
        id: "status",
        header: "Status",
        accessorFn: (d) => d.status,
        cell: ({ row }) => (
          <>
            <StatusChip status={row.original.status} />{" "}
            <span className={styles.meta}>{formatPercent(row.original.confidence)}</span>
          </>
        ),
      },
      {
        id: "received",
        header: "Received",
        accessorFn: (d) => d.received_at,
        cell: ({ row }) => formatDateTime(row.original.received_at),
      },
      {
        id: "channel",
        header: "Channel",
        accessorFn: (d) => `${d.channel}${d.classified_by ? ` · ${d.classified_by}` : ""}`,
      },
      {
        id: "actions",
        header: () => <span className="visually-hidden">Actions</span>,
        enableSorting: false,
        cell: ({ row }) => <DownloadButton docId={row.original.id} filename={row.original.original_name} size="sm" />,
      },
    ],
    [clientId],
  );

  return (
    <>
      <div
        className={[styles.drop, over ? styles.dropOver : ""].join(" ")}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
      >
        <label htmlFor={inputId}>
          <strong>Add documents</strong> — PDFs, photos of receipts, Word, Excel, CSV, emails (.eml), ZIPs. Several at
          once is fine; each gets its own result.
        </label>
        <input
          id={inputId}
          ref={inputRef}
          type="file"
          multiple
          disabled={!upload.allowed || busy !== null}
          aria-describedby={upload.allowed ? undefined : `${inputId}-reason`}
          onChange={(e) => {
            if (e.target.files) void uploadFiles(e.target.files);
          }}
          data-testid="upload-input"
        />
        {!upload.allowed ? (
          <p id={`${inputId}-reason`} className={styles.meta}>
            {upload.reason}
          </p>
        ) : null}
        {busy ? (
          <p className={styles.meta} role="status">
            Reading {busy}…
          </p>
        ) : null}
      </div>
      {results ? <PartialSuccess items={results} onDismiss={() => setResults(null)} /> : null}
      <Card>
        <p className={styles.hint}>
          The 100 most recent documents of this client (the full, paged list is an API change in progress).
        </p>
        <DataTable
          data={detail.documents}
          columns={columns}
          caption={`Documents of ${detail.client.name}`}
          getRowId={(d) => d.id}
          renderEmpty={() => (
            <Empty
              title="No documents yet"
              action={
                <Button
                  tone="primary"
                  disabledReason={upload.allowed ? undefined : upload.reason}
                  onClick={() => inputRef.current?.click()}
                >
                  Upload the first document
                </Button>
              }
            >
              <p>Everything uploaded here is read, classified and filed in this client's vault.</p>
            </Empty>
          )}
        />
      </Card>
    </>
  );
}
