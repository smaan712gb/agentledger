/** A post-sign-in destination must be a path on this origin: never a protocol-relative or absolute URL. */
export function safeNext(next: string | null | undefined): string | null {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.includes("\\")) return null;
  return next;
}
