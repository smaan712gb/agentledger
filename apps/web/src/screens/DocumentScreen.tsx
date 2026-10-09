import type { ClientDetail } from "@agentledger/contracts";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";

import { formatBytes, formatDateTime, formatPercent } from "../lib/format";
import { queries } from "../queries";
import { Card, PageHeader } from "../ui/Card";
import { StatusChip } from "../ui/Chip";
import { NotFound, QueryBoundary } from "../ui/states";
import { useClientDetail } from "./clientDetail";
import { DownloadButton } from "./DownloadButton";
import styles from "./screens.module.css";

export function DocumentScreen() {
  const { query } = useClientDetail();
  const { docId } = useParams({ from: "/_app/clients/$clientId/documents/$docId" });
  return <QueryBoundary query={query}>{(detail) => <Document detail={detail} docId={docId} />}</QueryBoundary>;
}

function Document({ detail, docId }: { detail: ClientDetail; docId: string }) {
  const doc = detail.documents.find((d) => d.id === docId);
  const versions = useQuery({ ...queries.versions(docId), enabled: Boolean(doc) });
  const back = (
    <Link to="/clients/$clientId/documents" params={{ clientId: detail.client.id }} search={(prev) => prev}>
      Back to documents
    </Link>
  );
  if (!doc) {
    return (
      <NotFound detail="This document is not among the client's 100 most recent, or it was removed." action={back} />
    );
  }
  return (
    <>
      <PageHeader
        title={doc.original_name}
        subtitle={
          <>
            <StatusChip status={doc.status} /> {doc.doc_type ?? "—"}
            {doc.tax_year ? ` · ${doc.tax_year}` : ""}
          </>
        }
        actions={<DownloadButton docId={doc.id} filename={doc.original_name} />}
      />
      <p className={styles.hint}>Files are downloaded, never shown inside the app; {back}.</p>
      <div className={styles.twoCol}>
        <Card title="Details">
          <dl className={styles.dl}>
            <dt>Summary</dt>
            <dd>{doc.summary ?? "—"}</dd>
            <dt>Confidence</dt>
            <dd>{formatPercent(doc.confidence)}</dd>
            <dt>Classified by</dt>
            <dd>{doc.classified_by ?? "—"}</dd>
            <dt>Received</dt>
            <dd>
              {formatDateTime(doc.received_at)} · {doc.channel}
            </dd>
            <dt>Filed under</dt>
            <dd>
              <code>{doc.vault_path ?? "—"}</code>
            </dd>
            <dt>Document id</dt>
            <dd>
              <code>{doc.id}</code>
            </dd>
          </dl>
        </Card>
        <Card title="Versions">
          <QueryBoundary query={versions} isEmpty={(v) => v.length === 0}>
            {(list) => (
              <table>
                <caption className="visually-hidden">Stored versions</caption>
                <thead>
                  <tr>
                    <th scope="col">Version</th>
                    <th scope="col">Size</th>
                    <th scope="col">Stored</th>
                    <th scope="col">By</th>
                    <th scope="col">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((v) => (
                    <tr key={v.version}>
                      <td>{v.version}</td>
                      <td>{formatBytes(v.size)}</td>
                      <td>{formatDateTime(v.created_at)}</td>
                      <td>{v.created_by}</td>
                      <td>{v.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </QueryBoundary>
        </Card>
      </div>
    </>
  );
}
