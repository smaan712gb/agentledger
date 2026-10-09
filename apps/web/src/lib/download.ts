import { api } from "../api";

/**
 * Downloads go through a signed link (POST /api/links, 120 seconds) so the session token never appears in a URL;
 * the file route answers with Content-Disposition: attachment, so the browser saves rather than renders. Uploaded
 * files are never rendered inline in this app (the sandboxed inline route is an API change for later).
 */
export async function downloadPath(path: string, filename?: string): Promise<void> {
  const link = await api.links.create(path);
  const a = document.createElement("a");
  a.href = link.url;
  a.rel = "noopener";
  if (filename) a.download = filename;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
}
