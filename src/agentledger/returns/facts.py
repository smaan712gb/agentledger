"""Facts never overwrite (backlog F-07, acceptance test Q16).

A return's inputs are a materialized view over fact assertions. Every accepted value is an assertion with its source
(a document and box, the preparer, a conflict resolution) and the assertion it supersedes, kept forever
(`fact_assertions`, values sealed with the firm key because they include identifiers).

Re-populating from documents never overwrites what is there:

* an empty field takes the document's value;
* the same value, or a value from the same document (a re-extraction), is applied;
* anything else (a preparer's entry, another document's value) becomes a **fact conflict**: the current value stays,
  and the return cannot go to review until a person keeps it or takes the document's;
* items a preparer added by hand are kept; document items are matched by document, never by position;
* an amount a document should carry but does not is reported as missing (it is never taken as zero).
"""

from __future__ import annotations

import copy
from decimal import Decimal, InvalidOperation
from typing import Any

from .. import audit
from ..db import one, rows, unit_of_work

# The amount without which a document item means nothing (a W-2 without wages is not a $0 W-2).
REQUIRED = {"w2s": "wages", "interest": "interest", "dividends": "ordinary", "retirement": "gross_distribution",
            "social_security": "net_benefits", "unemployment": "amount"}
SOURCES = ("document", "preparer", "resolution")


def _same(a: Any, b: Any) -> bool:
    if a == b:
        return True
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except (InvalidOperation, ValueError):
        return False


def _empty(v: Any) -> bool:
    return v is None or v == ""


def _doc_of(prov: dict[str, Any], lst: str, i: int) -> str | None:
    prefix = f"{lst}[{i}]."
    # A field the preparer edited still belongs to its document's item (previous_document), so the item is matched
    # to its document and never duplicated by the next population.
    docs = {p.get("document_id") or p.get("previous_document") for k, p in prov.items()
            if k.startswith(prefix) and (p.get("document_id") or p.get("previous_document"))}
    return next(iter(docs)) if len(docs) == 1 else None


def merge_population(cur_inputs: dict[str, Any], cur_prov: dict[str, Any], pop_inputs: dict[str, Any],
                     pop_prov: dict[str, Any], resolved_keep: set[tuple[str, str, str]] | None = None
                     ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Merge document values into the current inputs without overwriting.

    Returns (inputs, provenance, conflicts, issues, changed_paths). `resolved_keep` holds (anchor, document_id, value)
    choices already made, so a conflict someone resolved by keeping the current value is not raised again."""
    resolved_keep = resolved_keep or set()
    inputs = copy.deepcopy(cur_inputs)
    prov: dict[str, Any] = {}
    conflicts: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    changed: list[str] = []

    def field_merge(path: str, anchor: str, cur_v: Any, cur_p: dict[str, Any] | None, new_v: Any, new_p: dict[str, Any]) -> Any:
        doc = new_p.get("document_id", "")
        if _empty(cur_v):
            prov[path] = {**new_p, "confirmed": False}
            changed.append(path)
            return new_v
        if _same(cur_v, new_v):
            if cur_p is not None:
                prov[path] = cur_p
            elif cur_p is None and doc:      # a hand entry the document now corroborates: keep it as the preparer's
                pass
            return cur_v
        if cur_p is not None and cur_p.get("document_id") == doc and cur_p.get("source", "document") == "document":
            prov[path] = {**new_p, "confirmed": False}           # the same document, re-read: a correction of itself
            changed.append(path)
            return new_v
        if (anchor, doc, str(new_v)) not in resolved_keep:
            conflicts.append({"path": path, "anchor": anchor, "current": cur_v, "proposed": new_v, "document_id": doc,
                              "box": new_p.get("box"), "current_source": (cur_p or {}).get("source") or
                              ("document " + str(cur_p.get("document_id")) if cur_p else "preparer")})
        if cur_p is not None:
            prov[path] = cur_p
        return cur_v

    # Provenance of everything not touched by documents carries over unchanged (re-keyed for lists below).
    list_keys = {k for k, v in pop_inputs.items() if isinstance(v, list)} | {
        k for k, v in cur_inputs.items() if isinstance(v, list) and any(p.startswith(f"{k}[") for p in cur_prov)}
    for k, p in cur_prov.items():
        if k.split("[", 1)[0].split(".", 1)[0] not in list_keys:
            prov[k] = p

    for key, value in pop_inputs.items():
        if isinstance(value, list):
            old_items = list(cur_inputs.get(key) or [])
            old_by_doc: dict[str, tuple[int, dict[str, Any]]] = {}
            merged: list[dict[str, Any]] = []
            for i, item in enumerate(old_items):
                doc = _doc_of(cur_prov, key, i)
                if doc:
                    old_by_doc[doc] = (i, item)
                else:                                            # added by hand: always kept, provenance as it was
                    j = len(merged)
                    merged.append(copy.deepcopy(item))
                    for k, p in cur_prov.items():
                        if k.startswith(f"{key}[{i}]."):
                            prov[f"{key}[{j}]." + k.split("].", 1)[1]] = p
            seen: set[str] = set()
            for j_new, item in enumerate(value):
                doc = _doc_of(pop_prov, key, j_new)
                j = len(merged)
                if doc and doc in old_by_doc:
                    seen.add(doc)
                    i_old, old = old_by_doc[doc]
                    out = copy.deepcopy(old)
                    for f, new_v in item.items():
                        new_p = pop_prov.get(f"{key}[{j_new}].{f}")
                        if new_p is None:                       # names and other unsourced attributes: fill if empty
                            if _empty(out.get(f)):
                                out[f] = new_v
                            continue
                        out[f] = field_merge(f"{key}[{j}].{f}", f"{key}[{doc}].{f}", old.get(f),
                                             cur_prov.get(f"{key}[{i_old}].{f}"), new_v, new_p)
                    for k, p in cur_prov.items():                # provenance of fields the document no longer has
                        if k.startswith(f"{key}[{i_old}].") and f"{key}[{j}]." + k.split("].", 1)[1] not in prov:
                            prov[f"{key}[{j}]." + k.split("].", 1)[1]] = p
                    merged.append(out)
                else:
                    merged.append(copy.deepcopy(item))
                    for k, p in pop_prov.items():
                        if k.startswith(f"{key}[{j_new}]."):
                            path = f"{key}[{j}]." + k.split("].", 1)[1]
                            prov[path] = {**p, "confirmed": False}
                            changed.append(path)
            req = REQUIRED.get(key)
            for j, item in enumerate(merged):                   # judged on the merged value: a typed-in amount counts
                doc = _doc_of(prov, key, j)
                if req and doc and _empty(item.get(req)) and (f"missing:{key}[{doc}].{req}", doc, "None") not in resolved_keep:
                    issues.append({"document_id": doc, "code": "missing_value", "blocking": True, "path": f"{key}[{j}].{req}",
                                   "anchor": f"missing:{key}[{doc}].{req}",
                                   "message": f"{key} item from document {doc}: {req} could not be read; it is not taken as zero"})
            for doc, (i_old, old) in old_by_doc.items():        # never dropped silently
                if doc not in seen:
                    j = len(merged)
                    merged.append(copy.deepcopy(old))
                    for k, p in cur_prov.items():
                        if k.startswith(f"{key}[{i_old}]."):
                            prov[f"{key}[{j}]." + k.split("].", 1)[1]] = p
                    issues.append({"document_id": doc, "code": "document_no_longer_provides", "blocking": False,
                                   "message": f"{key} item from document {doc} is no longer populated by any document; "
                                              "it was kept. Remove it explicitly if it no longer applies."})
            inputs[key] = merged
        elif isinstance(value, dict):
            target = dict(inputs.get(key) or {})
            for f, new_v in value.items():
                path = f"{key}.{f}"
                new_p = pop_prov.get(path)
                if new_p is None:
                    if _empty(target.get(f)):
                        target[f] = new_v
                    continue
                target[f] = field_merge(path, path, target.get(f), cur_prov.get(path), new_v, new_p)
            inputs[key] = target
        elif _empty(inputs.get(key)):
            inputs[key] = value
    return inputs, prov, conflicts, issues, changed


def changed_paths(old_inputs: dict[str, Any], new_inputs: dict[str, Any]) -> list[str]:
    old_flat, new_flat = flatten(old_inputs), flatten(new_inputs)
    return sorted(p for p in set(old_flat) | set(new_flat) if not _same(old_flat.get(p), new_flat.get(p)))


def mark_edits(old_inputs: dict[str, Any], new_inputs: dict[str, Any], prov: dict[str, Any], actor: str) -> tuple[dict[str, Any], list[str]]:
    """A preparer's edit of a document-sourced value makes it the preparer's (the document stays on record)."""
    changed = changed_paths(old_inputs, new_inputs)
    out = dict(prov)
    for p in changed:
        if p in out and out[p].get("source", "document") == "document":
            out[p] = {"source": "preparer", "edited_by": actor, "previous_document": out[p].get("document_id"),
                      "previous_value": out[p].get("value"), "confirmed": True}
    return out, changed


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = obj
    return out


# ------------------------------------------------------------------------------------------------- the log
def record(conn: Any, sealer: Any, rid: str, inputs: dict[str, Any], prov: dict[str, Any], paths: list[str], *, actor: str,
           default_source: str = "preparer") -> int:
    """Append an assertion for each changed path, superseding the current one for that path."""
    flat = flatten(inputs)
    n = 0
    with unit_of_work(conn):
        for path in paths:
            p = prov.get(path) or {}
            source = p.get("source") or ("document" if p.get("document_id") else default_source)
            ref = p.get("document_id") or p.get("edited_by") or actor
            cur = one(conn, "SELECT id FROM fact_assertions WHERE return_id = ? AND path = ? ORDER BY id DESC LIMIT 1", rid, path)
            conn.execute("INSERT INTO fact_assertions (return_id, path, value, source, source_ref, asserted_by, asserted_at, supersedes) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (rid, path, sealer.seal({"v": flat.get(path)}, f"fact:{rid}"), source, ref, actor, audit.now(),
                          cur["id"] if cur else None))
            n += 1
    return n


def history(conn: Any, sealer: Any, rid: str, path: str) -> list[dict[str, Any]]:
    out = rows(conn, "SELECT * FROM fact_assertions WHERE return_id = ? AND path = ? ORDER BY id", rid, path)
    for a in out:
        a["value"] = sealer.open(a["value"], f"fact:{rid}")["v"]
    return out


def raise_conflicts(conn: Any, sealer: Any, rid: str, conflicts: list[dict[str, Any]], actor: str) -> list[int]:
    ids = []
    with unit_of_work(conn):
        for c in conflicts:
            if one(conn, "SELECT id FROM fact_conflicts WHERE return_id = ? AND anchor = ? AND document_id = ? AND resolved_at IS NULL",
                   rid, c["anchor"], c["document_id"]):
                continue
            cur = conn.execute("INSERT INTO fact_conflicts (return_id, path, anchor, document_id, box, current_value, proposed_value, "
                               "current_source, raised_by, raised_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (rid, c["path"], c["anchor"], c["document_id"], c.get("box"),
                                sealer.seal({"v": c["current"]}, f"fact:{rid}"), sealer.seal({"v": c["proposed"]}, f"fact:{rid}"),
                                c["current_source"], actor, audit.now()))
            ids.append(int(cur.lastrowid or 0))
    return ids


def open_conflicts(conn: Any, sealer: Any, rid: str) -> list[dict[str, Any]]:
    out = rows(conn, "SELECT * FROM fact_conflicts WHERE return_id = ? AND resolved_at IS NULL ORDER BY id", rid)
    for c in out:
        c["current_value"] = sealer.open(c["current_value"], f"fact:{rid}")["v"]
        c["proposed_value"] = sealer.open(c["proposed_value"], f"fact:{rid}")["v"]
    return out


def resolved_keeps(conn: Any, sealer: Any, rid: str) -> set[tuple[str, str, str]]:
    out = set()
    for c in rows(conn, "SELECT * FROM fact_conflicts WHERE return_id = ? AND resolution = 'kept'", rid):
        out.add((c["anchor"], c["document_id"], str(sealer.open(c["proposed_value"], f"fact:{rid}")["v"])))
    return out


def close_conflict(conn: Any, conflict_id: int, *, resolution: str, actor: str, note: str) -> None:
    if resolution not in ("kept", "replaced"):
        raise ValueError("resolution is 'kept' or 'replaced'")
    conn.execute("UPDATE fact_conflicts SET resolution = ?, resolved_by = ?, resolved_at = ?, note = ? WHERE id = ? AND resolved_at IS NULL",
                 (resolution, actor, audit.now(), note, conflict_id))


def locate(anchor: str, prov: dict[str, Any], inputs: dict[str, Any]) -> str | None:
    """Where a conflict's field is now: list items are found by their document, not by a position that may move."""
    if "[" not in anchor or anchor.startswith("missing:"):
        return anchor if not anchor.startswith("missing:") else None
    key, rest = anchor.split("[", 1)
    doc, field = rest.split("].", 1)
    for i in range(len(inputs.get(key) or [])):
        if _doc_of(prov, key, i) == doc:
            return f"{key}[{i}].{field}"
    return None


def set_path(inputs: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    out = copy.deepcopy(inputs)
    if "[" in path:
        key, rest = path.split("[", 1)
        idx, field = rest.split("].", 1)
        out[key][int(idx)][field] = value
    else:
        key, field = path.split(".", 1)
        out.setdefault(key, {})[field] = value
    return out
