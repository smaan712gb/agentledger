/**
 * Paths into the inputs object, in the API's own spelling: positional (`w2s[0].wages`, `taxpayer.ssn`,
 * `w2s[0].box12.D`) for provenance and 422 errors, and anchors (`w2s[<document>].wages`) for the fact log, where a
 * list item is named by the document it came from (returns/facts.py), never by a position that may move.
 */

export type Segment = string | number;

export const IDENTITY = "source_document";

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** "w2s[0].box12.D" -> ["w2s", 0, "box12", "D"]. Keys never contain "[", "]" or "." (the model's are identifiers). */
export function parsePath(path: string): Segment[] {
  const out: Segment[] = [];
  for (const part of path.split(".")) {
    const m = /^([^[]*)((?:\[\d+\])*)$/.exec(part);
    if (!m) {
      out.push(part);
      continue;
    }
    if (m[1]) out.push(m[1]);
    for (const idx of (m[2] ?? "").matchAll(/\[(\d+)\]/g)) out.push(Number(idx[1]));
  }
  return out;
}

export function joinPath(segments: Segment[]): string {
  let out = "";
  for (const s of segments) {
    if (typeof s === "number") out += `[${s}]`;
    else out += out ? `.${s}` : s;
  }
  return out;
}

export function getPath(obj: unknown, path: string): unknown {
  let cur: unknown = obj;
  for (const seg of parsePath(path)) {
    if (typeof seg === "number") {
      if (!Array.isArray(cur)) return undefined;
      cur = cur[seg];
    } else {
      if (!isRecord(cur)) return undefined;
      cur = cur[seg];
    }
  }
  return cur;
}

function without(obj: Record<string, unknown>, key: string): Record<string, unknown> {
  return Object.fromEntries(Object.entries(obj).filter(([k]) => k !== key));
}

function assign(container: unknown, segments: Segment[], value: unknown): unknown {
  const [head, ...rest] = segments;
  if (head === undefined) return value;
  if (typeof head === "number") {
    const list: unknown[] = Array.isArray(container) ? [...(container as unknown[])] : [];
    if (rest.length === 0 && value === undefined) {
      list.splice(head, 1);
      return list;
    }
    list[head] = assign(list[head], rest, value);
    return list;
  }
  const record = isRecord(container) ? container : {};
  if (rest.length === 0) return value === undefined ? without(record, head) : { ...record, [head]: value };
  return { ...record, [head]: assign(record[head], rest, value) };
}

/**
 * A copy of `obj` with the value at `path` replaced (immutable). `undefined` removes the key, or the list item: an
 * absent key is how the editor says "not stated" (the model's default applies, null for an Optional field).
 */
export function setPath(obj: Record<string, unknown>, path: string, value: unknown): Record<string, unknown> {
  const out = assign(obj, parsePath(path), value);
  return isRecord(out) ? out : {};
}

/** A 422 `loc` (["body", "w2s", 0, "wages"]) as a path. */
export function locToPath(loc: (string | number)[]): string {
  return joinPath(loc.filter((p, i) => !(i === 0 && p === "body")));
}

/** The top-level list a positional path points into (`w2s` for `w2s[0].wages`), else null. */
export function listOf(path: string): string | null {
  const [head, second] = parsePath(path);
  return typeof head === "string" && typeof second === "number" ? head : null;
}

export function itemIdentity(item: unknown): string | null {
  const id = isRecord(item) ? item[IDENTITY] : undefined;
  return typeof id === "string" && id ? id : null;
}

/**
 * The anchor of a positional path: a list item is named by its document (`w2s[doc_1].wages`) or, entered by hand, by
 * its position (`w2s[#0].wages`); anything else is its own anchor.
 */
export function anchorFor(path: string, inputs: Record<string, unknown>): string {
  const segments = parsePath(path);
  const [head, index, ...rest] = segments;
  if (typeof head !== "string" || typeof index !== "number") return path;
  const list = inputs[head];
  const item = Array.isArray(list) ? (list[index] as unknown) : undefined;
  const tag = itemIdentity(item) ?? `#${index}`;
  return `${head}[${tag}]${rest.length ? `.${joinPath(rest)}` : ""}`;
}

/** The positional path an anchor names today (the item found by its document), else null. */
export function locateAnchor(anchor: string, inputs: Record<string, unknown>): string | null {
  const bare = anchor.replace(/^(missing|orphan|duplicate):/, "");
  const m = /^([^[]+)\[([^\]]+)\](?:\.(.*))?$/.exec(bare);
  if (!m) return bare;
  const [, key = "", tag = "", field] = m;
  const list = inputs[key];
  if (!Array.isArray(list)) return null;
  const index = tag.startsWith("#")
    ? Number(tag.slice(1))
    : list.findIndex((item: unknown) => itemIdentity(item) === tag);
  if (index < 0 || index >= list.length) return null;
  return field ? `${key}[${index}].${field}` : `${key}[${index}]`;
}

/** The field part of an anchor or path (`wages` for `w2s[doc_1].wages`, `ssn` for `taxpayer.ssn`). */
export function leafOf(path: string): string {
  const segments = parsePath(path.replace(/^(missing|orphan|duplicate):/, "").replace(/\[[^\]]*\]/g, "[0]"));
  const last = segments[segments.length - 1];
  return typeof last === "string" ? last : "";
}
