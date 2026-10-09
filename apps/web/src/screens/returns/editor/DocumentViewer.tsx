import { ApiError, isApiError } from "@agentledger/contracts";
import { useEffect, useState } from "react";

import { api } from "../../../api";
import { Button } from "../../../ui/Button";
import { Conflict, ErrorState, Gone, Loading, NotFound } from "../../../ui/states";
import { DownloadButton } from "../../DownloadButton";
import screens from "../../screens.module.css";
import styles from "../returns.module.css";

type ViewerState =
  | { phase: "loading" }
  | { phase: "inline"; kind: "pdf" | "image"; url: string }
  | { phase: "download"; type: string }
  | { phase: "error"; error: unknown };

async function errorDetail(response: Response): Promise<string> {
  const text = await response.text().catch(() => "");
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (typeof parsed.detail === "string") return parsed.detail;
  } catch {
    /* not JSON */
  }
  return text || response.statusText;
}

/**
 * The document beside the field, through a signed link (POST /api/links) and GET /api/documents/{id}/file?inline=1:
 * the API serves a PDF, PNG or JPEG inline with its real media type inside a sandbox, and everything else as an
 * attachment, which is offered as a download here. There are no box coordinates yet, so the box is named, not
 * highlighted.
 */
export function DocumentViewer({
  documentId,
  name,
  box,
  value,
  onClose,
}: {
  documentId: string;
  name: string | undefined;
  /** The box label the field came from ("Box 1 — Wages, tips, other compensation"). */
  box: string;
  value: string | undefined;
  onClose: () => void;
}) {
  const [state, setState] = useState<ViewerState>({ phase: "loading" });

  useEffect(() => {
    // Read through a function: the flag is set by the cleanup, which control-flow analysis cannot see.
    let cancelled = false;
    const isCancelled = () => cancelled;
    let objectUrl: string | null = null;
    const load = async () => {
      const link = await api.links.create(api.documents.filePath(documentId));
      const url = api.documents.inlineUrl(link.url);
      const response = await fetch(url);
      if (isCancelled()) return;
      if (!response.ok) {
        throw new ApiError({
          status: response.status,
          detail: await errorDetail(response),
          requestId: response.headers.get("X-Request-Id"),
          path: url,
          method: "GET",
        });
      }
      const disposition = response.headers.get("Content-Disposition") ?? "";
      const type = response.headers.get("Content-Type") ?? "";
      if (!disposition.startsWith("inline")) {
        await response.body?.cancel().catch(() => undefined);
        setState({ phase: "download", type });
        return;
      }
      if (type.startsWith("image/")) {
        const blob = await response.blob();
        if (isCancelled()) return;
        objectUrl = URL.createObjectURL(blob);
        setState({ phase: "inline", kind: "image", url: objectUrl });
        return;
      }
      await response.body?.cancel().catch(() => undefined);
      setState({ phase: "inline", kind: "pdf", url });
    };
    load().catch((error: unknown) => {
      if (!isCancelled()) setState({ phase: "error", error });
    });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [documentId]);

  const title = name ?? documentId;
  return (
    <div data-testid="viewer" data-document={documentId}>
      <p>
        <strong>{title}</strong> <code className={styles.code}>{documentId}</code>
      </p>
      <p>
        <strong>{box}</strong>
        {value !== undefined ? `: the document gave ${value}` : ""}. No box coordinates yet: find it on the page.
      </p>
      {state.phase === "loading" ? <Loading label="Opening the document" /> : null}
      {state.phase === "inline" && state.kind === "pdf" ? (
        <iframe src={state.url} className={styles.viewerFrame} title={`${title}, ${box}`} data-testid="viewer-frame" />
      ) : null}
      {state.phase === "inline" && state.kind === "image" ? (
        <img src={state.url} className={styles.viewerImage} alt={`${title}: the page carrying ${box}`} />
      ) : null}
      {state.phase === "download" ? (
        <p className={screens.hint} data-testid="viewer-download">
          This file type ({state.type || "unknown"}) is not shown inside the app; download it to read the box.
        </p>
      ) : null}
      {state.phase === "error" ? <ViewerError error={state.error} /> : null}
      <div className={screens.actions}>
        <DownloadButton docId={documentId} filename={title} size="sm" />
        <Button size="sm" tone="ghost" onClick={onClose}>
          Close document
        </Button>
      </div>
    </div>
  );
}

function ViewerError({ error }: { error: unknown }) {
  if (isApiError(error)) {
    const detail = typeof error.detail === "string" ? error.detail : error.message;
    if (error.status === 410) return <Gone detail={detail} />;
    if (error.status === 409) return <Conflict detail={detail} />;
    if (error.status === 404) return <NotFound detail={detail} />;
    return <ErrorState detail={detail} requestId={error.requestId} />;
  }
  return <ErrorState detail={error instanceof Error ? error.message : undefined} />;
}
