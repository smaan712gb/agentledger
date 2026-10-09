import { ApiError, isApiError } from "@agentledger/contracts";
import { useState } from "react";

import { api } from "../api";
import { Button } from "../ui/Button";
import { Conflict, Gone, NotFound } from "../ui/states";
import { useToast } from "../ui/Toast";

/**
 * Fetches the file through a signed link (POST /api/links, 120 s) and hands it to the browser as a download, so a
 * 410 (deleted under retention, with its receipt) or a 409 (the stored bytes failed their integrity check) is shown
 * as a state here instead of a JSON page replacing the app.
 */
export async function fetchDocumentBlob(docId: string): Promise<{ blob: Blob; filename: string | null }> {
  const link = await api.links.create(api.documents.filePath(docId));
  const response = await fetch(link.url);
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    let detail: string = text || response.statusText;
    try {
      const parsed = JSON.parse(text) as { detail?: unknown };
      if (typeof parsed.detail === "string") detail = parsed.detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError({
      status: response.status,
      detail,
      requestId: response.headers.get("X-Request-Id"),
      path: link.url,
      method: "GET",
    });
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename="([^"]+)"/.exec(disposition);
  return { blob: await response.blob(), filename: match?.[1] ?? null };
}

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export function DownloadButton({
  docId,
  filename,
  size = "md",
}: {
  docId: string;
  filename: string;
  size?: "sm" | "md";
}) {
  const { toast } = useToast();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ApiError | null>(null);

  async function download() {
    setBusy(true);
    setProblem(null);
    try {
      const { blob, filename: served } = await fetchDocumentBlob(docId);
      saveBlob(blob, served ?? filename);
    } catch (err) {
      if (isApiError(err) && [404, 409, 410].includes(err.status)) setProblem(err);
      else toast(isApiError(err) ? err.message : "The download failed.", "error");
    } finally {
      setBusy(false);
    }
  }

  if (problem) {
    const detail = typeof problem.detail === "string" ? problem.detail : problem.message;
    if (problem.status === 410) return <Gone detail={detail} />;
    if (problem.status === 409) return <Conflict detail={detail} onRefresh={() => setProblem(null)} />;
    return <NotFound detail={detail} />;
  }

  return (
    <Button size={size} busy={busy} onClick={download} aria-label={`Download ${filename}`}>
      Download
    </Button>
  );
}
