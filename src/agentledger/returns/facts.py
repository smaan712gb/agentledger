"""Facts never overwrite (backlog F-07, acceptance test Q16; hardened after the re-audit of 952ee96).

A return's inputs are a materialized view over fact assertions. Every accepted value is an assertion with its source
(a document and box, the preparer, a conflict resolution) and the assertion it supersedes, kept forever
(`fact_assertions`, values sealed with the firm key because they include identifiers). Assertions are keyed by a stable
anchor: a list item is named by the document it came from (`w2s[d_w2a].wages`), never by its position.

Re-populating from documents never overwrites what is there:

* an empty field takes the document's value;
* the same value, or a value from the same documents (a re-read), is applied;
* anything else (a preparer's entry, another document's value) becomes a **fact conflict**: the current value stays,
  and the return cannot go to review until a person keeps it or takes the documents' value. Taking it re-reads the
  documents first, so only what they say at that moment is ever applied;
* items a preparer added by hand are kept; document items carry their document (`source_document`) and are matched
  by it. An item whose document left the return (moved, re-dated, deleted) stays and blocks review until a person
  removes it or keeps it with a reason;
* an item without its required amount (a W-2 without wages), without an amount another amount implies (Medicare
  wages when Medicare tax was withheld), without a valid code where one is required (a 1099-R's box 7), or without an
  amount its document carries but could not be read, is reported as missing and blocks review on every check,
  whatever was decided before: it is never taken as zero or as a default;
* a document item's identity is assigned by population and cannot be added, copied or duplicated by hand
  (`check_identities`); a decision to keep a value covers that value only.
"""

from __future__ import annotations

import copy
from decimal import Decimal, InvalidOperation
from typing import Any

from .. import audit
from ..db import one, rows, unit_of_work

# The amount without which a document item means nothing (a W-2 without wages is not a $0 W-2; a 1099-SA without its
# gross distribution, a 5498-SA without the year's contributions, a 5498 without the year-end value every trustee must
# report (Form 8606 line 6 needs it), a 1095-A without its annual premium).
REQUIRED = {"w2s": "wages", "interest": "interest", "dividends": "ordinary", "retirement": "gross_distribution",
            "social_security": "net_benefits", "unemployment": "amount", "hsa_distributions": "gross_distribution",
            "hsa_contributions": "total_contributions", "ira_accounts": "fmv", "marketplace_coverage": "annual_premium"}
# Amounts another amount implies: tax withheld on wages means there are such wages. Without them the withholding is
# credited against nothing (Form 8959 line 24 refunds all of box 6; the excess Social Security credit). Advance premium
# tax credit paid (1095-A column C) implies a second-lowest-cost silver plan premium (column B): Form 8962 cannot
# reconcile the advance without it.
IMPLIED = {"w2s": (("medicare_wages", "medicare_tax"), ("ss_wages", "ss_tax")),
           "marketplace_coverage": (("annual_slcsp", "annual_aptc"),)}
# Codes that are required and never defaulted: a 1099-R's box 7 decides the 10% (or 25%) additional tax; a 1099-SA's
# box 3 decides whether an HSA distribution is normal, an excess, a disability or a death distribution (Form 8889 Part II).
CODED = {"retirement": "distribution_code", "hsa_distributions": "distribution_code"}
DISTRIBUTION_CODES = frozenset("123456789ABCDEFGHJKLMNPQRSTUWY")
HSA_DISTRIBUTION_CODES = frozenset("123456")           # Form 1099-SA box 3
CODE_SETS = {"retirement": (DISTRIBUTION_CODES, 2), "hsa_distributions": (HSA_DISTRIBUTION_CODES, 1)}
SOURCES = ("document", "preparer", "resolution")
IDENTITY = "source_document"
RESOLUTIONS = ("kept", "replaced", "superseded")


class InputRejected(ValueError):
    """A change to a return's inputs that is refused: a hand-made document identity, an unknown input."""


def valid_code(v: Any, lst: str = "retirement") -> bool:
    """A required code: a 1099-R box 7 entry of one or two distribution codes (for example 7, 1, G, 7D), or a 1099-SA
    box 3 entry of one code 1-6."""
    code = str(v or "").strip()
    chars, width = CODE_SETS[lst]
    return 1 <= len(code) <= width and all(ch in chars for ch in code)


def _positive(v: Any) -> bool:
    try:
        return Decimal(str(v).replace(",", "")) > 0
    except (InvalidOperation, ValueError):
        return False


def _name(item: dict[str, Any]) -> Any:
    return item.get("employer_name") or item.get("payer") or item.get("trustee") or item.get("issuer") or item.get("owner")


def same(a: Any, b: Any) -> bool:
    if a == b:
        return True
    if a is None or b is None or a == "" or b == "":
        return False
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except (InvalidOperation, ValueError):
        return False


_same = same


def empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


_empty = empty


def item_doc(item: Any, prov: dict[str, Any], lst: str, i: int) -> str | None:
    """The document a list item came from: its own `source_document`, or (for items stored before identity was
    carried in the data) the single document its provenance names, including a field the preparer edited."""
    if isinstance(item, dict) and item.get(IDENTITY):
        return str(item[IDENTITY])
    prefix = f"{lst}[{i}]."
    docs = {p.get("document_id") or p.get("previous_document") for k, p in prov.items()
            if k.startswith(prefix) and (p.get("document_id") or p.get("previous_document"))}
    return next(iter(docs)) if len(docs) == 1 else None


def _sources(p: dict[str, Any] | None) -> set[str]:
    if not p:
        return set()
    return {x["document_id"] for x in p.get("documents", []) if x.get("document_id")} or ({p["document_id"]} if p.get("document_id") else set())


def satisfied(lst: str, field: str, value: Any) -> bool:
    """Whether a value answers a missing-amount question: present, a valid code where a code is required, and positive
    where another amount implies it (Medicare tax withheld on zero Medicare wages is not an answer)."""
    if empty(value):
        return False
    if CODED.get(lst) == field:
        return valid_code(value, lst)
    if any(f == field for f, _ in IMPLIED.get(lst, ())):
        return _positive(value)
    return True


def missing_required(inputs: dict[str, Any], prov: dict[str, Any] | None = None,
                     unreadable: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Every item without an amount it must have, wherever it came from (a document, a hand entry, an edit, a copy):
    its required amount, an amount another amount implies, a required code, or an amount its document carries but
    could not be read (`unreadable`, from documents.populate) that nobody has entered since."""
    out: list[dict[str, Any]] = []

    def add(key: str, j: int, item: dict[str, Any], field: str, why: str) -> None:
        doc = item_doc(item, prov or {}, key, j)
        anchor = f"missing:{key}[{doc or '#' + str(j)}].{field}"
        if any(m["anchor"] == anchor for m in out):
            return
        out.append({"path": f"{key}[{j}].{field}", "anchor": anchor, "document_id": doc, "list": key, "field": field,
                    "name": _name(item), "why": why})

    for key, items in inputs.items():
        if not isinstance(items, list):
            continue
        for j, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            req = REQUIRED.get(key)
            if req and empty(item.get(req)):
                add(key, j, item, req, "required")
            for field, implied_by in IMPLIED.get(key, ()):
                if not _positive(item.get(field)) and _positive(item.get(implied_by)):
                    add(key, j, item, field, f"implied by {implied_by}")
            code = CODED.get(key)
            if code and not satisfied(key, code, item.get(code)):
                add(key, j, item, code, "a valid code is required")
    for u in unreadable or []:
        # A single-valued group amount (a 1098 total, a prior-year return line) is judged on the group, whether or not
        # the group is on the return yet: an unread amount is missing until a person enters it.
        if isinstance(inputs.get(u["list"]), dict) or u.get("summed") or u.get("group"):
            path = f"{u['list']}.{u['field']}"
            p = (prov or {}).get(path)
            if empty((inputs.get(u["list"]) or {}).get(u["field"])) or (p is not None and p.get("source", "document") == "document"):
                anchor = f"missing:{path}"
                if not any(m["anchor"] == anchor for m in out):
                    out.append({"path": path, "anchor": anchor, "document_id": u["document_id"], "list": u["list"],
                                "field": u["field"], "name": None,
                                "why": f"{u['box']} of {u['document_id']} could not be read; a person enters the total"})
            continue
        for j, item in enumerate(inputs.get(u["list"]) or []):
            if isinstance(item, dict) and item_doc(item, prov or {}, u["list"], j) == u["document_id"]:
                value: Any = item
                for part in u["field"].split("."):                  # box12.TT is a code inside box 12
                    value = value.get(part) if isinstance(value, dict) else None
                if not satisfied(u["list"], u["field"], value):
                    add(u["list"], j, item, u["field"], f"{u['box']} could not be read")
    return out


def carry_identity(old_inputs: dict[str, Any], old_prov: dict[str, Any], new_inputs: dict[str, Any]) -> dict[str, Any]:
    """Items stored before identities were carried in the data (952ee96 and earlier) are known only by provenance at a
    position. A person's edit sends them back without `source_document`; an item at the same position with the same
    name keeps its document, once, so an unrelated edit never strips it (and the next population never duplicates
    it)."""
    out = copy.deepcopy(new_inputs)
    for key, items in out.items():
        old_items = old_inputs.get(key)
        if not isinstance(items, list) or not isinstance(old_items, list):
            continue
        legacy = []                                               # (position, item, document, its sourced values)
        for i, old in enumerate(old_items):
            if isinstance(old, dict) and not old.get(IDENTITY):
                doc = item_doc(old, old_prov, key, i)
                if doc:
                    sourced = {k.split("].", 1)[1]: old.get(k.split("].", 1)[1]) for k, p in old_prov.items()
                               if k.startswith(f"{key}[{i}].") and p.get("document_id") == doc}
                    legacy.append((i, old, doc, sourced))
        taken = {str(it[IDENTITY]) for it in items if isinstance(it, dict) and it.get(IDENTITY)}
        for j, it in enumerate(items):
            if not isinstance(it, dict) or it.get(IDENTITY):
                continue
            same_values = [doc for _, old, doc, sourced in legacy if doc not in taken and _name(old) == _name(it)
                           and sourced and all(same(it.get(f), v) for f, v in sourced.items())]
            by_position = [doc for i, old, doc, _ in legacy if i == j and doc not in taken and _name(old) == _name(it)]
            match = same_values if len(same_values) == 1 else by_position
            if len(match) == 1:
                it[IDENTITY] = match[0]
                taken.add(match[0])
    return out


def check_identities(old_inputs: dict[str, Any], old_prov: dict[str, Any], new_inputs: dict[str, Any]) -> None:
    """A document item's identity is assigned by population. A person may remove an item (and its identity with it),
    never add one, copy one onto another item, or move one to another list: anything else would let a hand entry
    claim a document's provenance, or hide a filed document from the "not on the return" check."""
    for key, items in new_inputs.items():
        if not isinstance(items, list):
            continue
        ids = [str(it[IDENTITY]) for it in items if isinstance(it, dict) and it.get(IDENTITY)]
        dup = sorted({d for d in ids if ids.count(d) > 1})
        if dup:
            raise InputRejected(f"{key}: the item from document {', '.join(dup)} appears more than once; a document's item is "
                             "on the return once (enter another item without its source_document)")
        allowed = {item_doc(it, old_prov, key, i) for i, it in enumerate(old_inputs.get(key) or [])} - {None}
        foreign = sorted(set(ids) - allowed)
        if foreign:
            raise InputRejected(f"{key}: {', '.join(foreign)} is not the document of an item already in {key}; document items "
                             "are added by populating from documents, not by hand")


def merge_population(cur_inputs: dict[str, Any], cur_prov: dict[str, Any], pop_inputs: dict[str, Any],
                     pop_prov: dict[str, Any], resolved_keep: set[tuple[str, str, str, str]] | None = None,
                     unreadable: list[dict[str, Any]] | None = None
                     ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Merge document values into the current inputs without overwriting.

    Returns (inputs, provenance, conflicts, issues, changed_paths). `resolved_keep` holds (anchor, document_id,
    document value, kept value) decisions to keep a current value over a document's value: that exact disagreement is
    not raised again, and only while the return still holds the value that was kept. It never exempts a missing
    amount. `unreadable` lists amounts the documents carry but could not be read; each stays missing until entered."""
    resolved_keep = resolved_keep or set()
    inputs = copy.deepcopy(cur_inputs)
    prov: dict[str, Any] = {}
    conflicts: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    changed: list[str] = []

    def field_merge(path: str, anchor: str, cur_v: Any, cur_p: dict[str, Any] | None, new_v: Any, new_p: dict[str, Any]) -> Any:
        doc = new_p.get("document_id", "")
        document_sourced = cur_p is not None and cur_p.get("source", "document") == "document"
        if empty(cur_v):
            prov[path] = {**new_p, "confirmed": False}
            changed.append(path)
            return new_v
        if same(cur_v, new_v):
            if cur_p is not None and document_sourced:   # keep the confirmation only if the same documents still say it
                prov[path] = {**new_p, "confirmed": bool(cur_p.get("confirmed")) and _sources(cur_p) == _sources(new_p)}
            elif cur_p is not None:
                prov[path] = cur_p
            return cur_v
        if document_sourced and _sources(cur_p) == _sources(new_p):
            prov[path] = {**new_p, "confirmed": False}           # the same documents, re-read: a correction of themselves
            changed.append(path)
            return new_v
        if (anchor, doc, str(new_v), str(cur_v)) not in resolved_keep:
            conflicts.append({"path": path, "anchor": anchor, "current": cur_v, "proposed": new_v, "document_id": doc,
                              "box": new_p.get("box"), "current_source": (cur_p or {}).get("source") or
                              ("document " + str(cur_p.get("document_id")) if cur_p else "preparer")})
        if cur_p is not None:
            prov[path] = cur_p
        return cur_v

    list_keys = {k for k, v in pop_inputs.items() if isinstance(v, list)} | {
        k for k, v in cur_inputs.items() if isinstance(v, list)}
    for k, p in cur_prov.items():                                  # untouched provenance carries over
        if k.split("[", 1)[0].split(".", 1)[0] not in list_keys:
            prov[k] = p

    for key in sorted(list_keys):
        has_documents = any(item_doc(it, cur_prov, key, i) for i, it in enumerate(cur_inputs.get(key) or []))
        if key not in pop_inputs and not has_documents:            # a hand-entered list no document touches
            for k, p in cur_prov.items():
                if k.startswith(f"{key}["):
                    prov[k] = p
            continue
        value = pop_inputs.get(key) or []                           # its last document may have left
        old_items = list(cur_inputs.get(key) or [])
        old_by_doc: dict[str, tuple[int, dict[str, Any]]] = {}
        merged: list[dict[str, Any]] = []
        for i, item in enumerate(old_items):
            doc = item_doc(item, cur_prov, key, i)
            if doc and doc in old_by_doc:                           # never dropped: kept as it is, and a person decides
                j = len(merged)
                merged.append(copy.deepcopy(item))
                for k, p in cur_prov.items():
                    if k.startswith(f"{key}[{i}]."):
                        prov[f"{key}[{j}]." + k.split("].", 1)[1]] = p
                issues.append({"document_id": doc, "code": "duplicate_identity", "blocking": True, "path": f"{key}[{j}]",
                               "anchor": f"duplicate:{key}[{doc}]",
                               "message": f"{key}: more than one item claims document {doc}; remove the copy or enter it "
                                          "without its source_document."})
            elif doc:
                old_by_doc[doc] = (i, item)
            else:                                                   # added by hand: always kept, provenance as it was
                j = len(merged)
                merged.append(copy.deepcopy(item))
                for k, p in cur_prov.items():
                    if k.startswith(f"{key}[{i}]."):
                        prov[f"{key}[{j}]." + k.split("].", 1)[1]] = p
        seen: set[str] = set()
        for j_new, item in enumerate(value):
            doc = item_doc(item, pop_prov, key, j_new)
            j = len(merged)
            if doc and doc in old_by_doc:
                seen.add(doc)
                i_old, old = old_by_doc[doc]
                out = copy.deepcopy(old)
                out[IDENTITY] = doc
                for f, new_v in item.items():
                    if f == IDENTITY:
                        continue
                    if isinstance(new_v, dict):                     # box 12: each code is its own sourced amount
                        sub = dict(out.get(f) or {}) if isinstance(out.get(f), dict) else {}
                        for code, v in new_v.items():
                            sp = pop_prov.get(f"{key}[{j_new}].{f}.{code}")
                            if sp is None:
                                if empty(sub.get(code)):
                                    sub[code] = v
                                continue
                            sub[code] = field_merge(f"{key}[{j}].{f}.{code}", f"{key}[{doc}].{f}.{code}", sub.get(code),
                                                    cur_prov.get(f"{key}[{i_old}].{f}.{code}"), v, sp)
                        out[f] = sub
                        continue
                    new_p = pop_prov.get(f"{key}[{j_new}].{f}")
                    if new_p is None:                               # names and other unsourced attributes: fill if empty
                        if empty(out.get(f)):
                            out[f] = new_v
                        continue
                    out[f] = field_merge(f"{key}[{j}].{f}", f"{key}[{doc}].{f}", old.get(f),
                                         cur_prov.get(f"{key}[{i_old}].{f}"), new_v, new_p)
                for k, p in cur_prov.items():                       # provenance of fields the document no longer has
                    if k.startswith(f"{key}[{i_old}].") and f"{key}[{j}]." + k.split("].", 1)[1] not in prov:
                        prov[f"{key}[{j}]." + k.split("].", 1)[1]] = p
                merged.append(out)
            else:
                new_item = copy.deepcopy(item)
                if doc:
                    new_item[IDENTITY] = doc
                merged.append(new_item)
                for k, p in pop_prov.items():
                    if k.startswith(f"{key}[{j_new}]."):
                        path = f"{key}[{j}]." + k.split("].", 1)[1]
                        prov[path] = {**p, "confirmed": False}
                        changed.append(path)
        for doc, (i_old, old) in old_by_doc.items():                # never dropped silently
            if doc not in seen:
                j = len(merged)
                merged.append(copy.deepcopy(old))
                for k, p in cur_prov.items():
                    if k.startswith(f"{key}[{i_old}]."):
                        prov[f"{key}[{j}]." + k.split("].", 1)[1]] = p
                issues.append({"document_id": doc, "code": "document_no_longer_provides", "blocking": True,
                               "path": f"{key}[{j}]", "anchor": f"orphan:{key}[{doc}]",
                               "message": f"{key} item from document {doc}: the document no longer belongs to this return "
                                          "(moved, re-dated or deleted). Remove the item, or keep it with a reason."})
        inputs[key] = merged

    for key, value in pop_inputs.items():
        if isinstance(value, list):
            continue
        if isinstance(value, dict):
            target = dict(inputs.get(key) or {})
            for f, new_v in value.items():
                path = f"{key}.{f}"
                new_p = pop_prov.get(path)
                if new_p is None:
                    if empty(target.get(f)):
                        target[f] = new_v
                    continue
                target[f] = field_merge(path, path, target.get(f), cur_prov.get(path), new_v, new_p)
            inputs[key] = target
        elif empty(inputs.get(key)):
            inputs[key] = value

    for path, p in sorted(cur_prov.items()):                         # a summed amount whose documents all left
        if "[" in path or "." not in path or p.get("source", "document") != "document" or not _sources(p):
            continue
        key, f = path.split(".", 1)
        if f not in (pop_inputs.get(key) or {}) and not empty((inputs.get(key) or {}).get(f)):
            issues.append({"document_id": p.get("document_id"), "code": "document_no_longer_provides", "blocking": True,
                           "path": path, "anchor": f"orphan:{path}",
                           "message": f"{path}: its documents ({', '.join(sorted(_sources(p)))}) no longer belong to this "
                                      "return (moved, re-dated or deleted). Remove the amount, or keep it with a reason."})

    for m in missing_required(inputs, prov, unreadable):             # judged on the merged return, every time
        issues.append({"document_id": m["document_id"], "code": "missing_value", "blocking": True, "path": m["path"],
                       "anchor": m["anchor"],
                       "message": f"{m['list']} item {m['name'] or ''} ({m['document_id'] or 'entered by hand'}): "
                                  f"{m['field']} is missing ({m['why']}); it is never taken as zero or a default"})
    return inputs, prov, conflicts, issues, changed


# ------------------------------------------------------------------------------------------------- anchors
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


def anchored(inputs: dict[str, Any], prov: dict[str, Any]) -> dict[str, tuple[str, Any]]:
    """anchor -> (current positional path, value). Document items are named by their document, others by position."""
    out: dict[str, tuple[str, Any]] = {}
    for k, v in inputs.items():
        if isinstance(v, list):
            for j, item in enumerate(v):
                if isinstance(item, dict):
                    tag = item_doc(item, prov, k, j) or f"#{j}"
                    for sub, val in flatten({f: x for f, x in item.items() if f != IDENTITY}).items():
                        out[f"{k}[{tag}].{sub}"] = (f"{k}[{j}].{sub}", val)
                else:
                    out[f"{k}[#{j}]"] = (f"{k}[{j}]", item)
        elif isinstance(v, dict):
            for sub, val in flatten(v).items():
                out[f"{k}.{sub}"] = (f"{k}.{sub}", val)
        else:
            out[k] = (k, v)
    return out


def changed_anchors(old_inputs: dict[str, Any], old_prov: dict[str, Any], new_inputs: dict[str, Any],
                    new_prov: dict[str, Any]) -> list[str]:
    a, b = anchored(old_inputs, old_prov), anchored(new_inputs, new_prov)
    return sorted(x for x in set(a) | set(b) if not same(a.get(x, ("", None))[1], b.get(x, ("", None))[1]))


def mark_edits(old_inputs: dict[str, Any], new_inputs: dict[str, Any], prov: dict[str, Any], actor: str) -> tuple[dict[str, Any], list[str]]:
    """A person's edit. List provenance follows each item's document (an item removed by hand takes its provenance with
    it; the others keep theirs, whatever their new position), and an edited document value becomes the preparer's,
    with the document's value kept on record. Returns (provenance, changed anchors)."""
    list_keys = {k for k, v in {**old_inputs, **new_inputs}.items() if isinstance(v, list)}
    out = {k: p for k, p in prov.items() if k.split("[", 1)[0].split(".", 1)[0] not in list_keys}
    for key in list_keys:
        old_items = old_inputs.get(key) or []
        old_by_doc = {}
        for i, it in enumerate(old_items):
            d = item_doc(it, prov, key, i)
            if d:
                old_by_doc[d] = i
        for j, it in enumerate(new_inputs.get(key) or []):
            d = str(it[IDENTITY]) if isinstance(it, dict) and it.get(IDENTITY) else None
            if d and d in old_by_doc:
                i = old_by_doc[d]
                for k, p in prov.items():
                    if k.startswith(f"{key}[{i}]."):
                        out[f"{key}[{j}]." + k.split("].", 1)[1]] = p
    changed = changed_anchors(old_inputs, prov, new_inputs, out)
    now = anchored(new_inputs, out)
    for a in changed:
        if a not in now:
            if "[" not in a:                                          # a removed amount takes its provenance with it
                out.pop(a, None)
            continue                                                  # removed: nothing left to attribute
        path = now[a][0]
        p = out.get(path)
        if p is not None and p.get("source", "document") != "preparer":
            out[path] = {"source": "preparer", "edited_by": actor, "previous_document": p.get("document_id"),
                         "previous_value": p.get("value"), "previous_source": p.get("source", "document"), "confirmed": True}
    return out, changed


# ------------------------------------------------------------------------------------------------- the log
def record(conn: Any, sealer: Any, rid: str, inputs: dict[str, Any], prov: dict[str, Any], anchors: list[str], *, actor: str,
           default_source: str = "preparer") -> int:
    """Append an assertion for each changed anchor, superseding the current one for that anchor."""
    now = anchored(inputs, prov)
    n = 0
    with unit_of_work(conn):
        for a in anchors:
            path, value = now.get(a, ("", None))
            p = prov.get(path) or {}
            source = p.get("source") or ("document" if p.get("document_id") else default_source)
            ref = p.get("document_id") or p.get("edited_by") or actor
            cur = one(conn, "SELECT id FROM fact_assertions WHERE return_id = ? AND path = ? ORDER BY id DESC LIMIT 1", rid, a)
            conn.execute("INSERT INTO fact_assertions (return_id, path, value, source, source_ref, asserted_by, asserted_at, supersedes) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (rid, a, sealer.seal({"v": value}, f"fact:{rid}"), source if a in now else "removed", ref, actor,
                          audit.now(), cur["id"] if cur else None))
            n += 1
    return n


def history(conn: Any, sealer: Any, rid: str, anchor: str) -> list[dict[str, Any]]:
    out = rows(conn, "SELECT * FROM fact_assertions WHERE return_id = ? AND path = ? ORDER BY id", rid, anchor)
    for a in out:
        a["value"] = sealer.open(a["value"], f"fact:{rid}")["v"]
    return out


# ------------------------------------------------------------------------------------------------- conflicts
def raise_conflicts(conn: Any, sealer: Any, rid: str, conflicts: list[dict[str, Any]], actor: str) -> list[int]:
    """At most one open conflict per anchor (the caller retires outdated ones first)."""
    ids = []
    with unit_of_work(conn):
        for c in conflicts:
            # The partial unique index (one open conflict per anchor) decides between concurrent populations.
            cur = conn.execute("INSERT INTO fact_conflicts (return_id, path, anchor, document_id, box, current_value, proposed_value, "
                               "current_source, raised_by, raised_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                               "ON CONFLICT (return_id, anchor) WHERE resolved_at IS NULL DO NOTHING",
                               (rid, c["path"], c["anchor"], c["document_id"] or "", c.get("box"),
                                sealer.seal({"v": c["current"]}, f"fact:{rid}"), sealer.seal({"v": c["proposed"]}, f"fact:{rid}"),
                                c["current_source"], actor, audit.now()))
            if cur.rowcount == 1 and cur.lastrowid:
                ids.append(int(cur.lastrowid))
    return ids


def open_conflicts(conn: Any, sealer: Any, rid: str) -> list[dict[str, Any]]:
    out = rows(conn, "SELECT * FROM fact_conflicts WHERE return_id = ? AND resolved_at IS NULL ORDER BY id", rid)
    for c in out:
        c["current_value"] = sealer.open(c["current_value"], f"fact:{rid}")["v"]
        c["proposed_value"] = sealer.open(c["proposed_value"], f"fact:{rid}")["v"]
    return out


def resolved_keeps(conn: Any, sealer: Any, rid: str) -> set[tuple[str, str, str, str]]:
    """Decisions to keep a current value over a document's value: (anchor, document, document value, kept value). A
    decision covers the value that was kept and nothing else. Missing amounts and orphaned items are never exempted
    this way: they are checked again every time."""
    out = set()
    for c in rows(conn, "SELECT * FROM fact_conflicts WHERE return_id = ? AND resolution = 'kept'", rid):
        if c["anchor"].startswith(("missing:", "orphan:", "duplicate:")):
            continue
        out.add((c["anchor"], c["document_id"], str(sealer.open(c["proposed_value"], f"fact:{rid}")["v"]),
                 str(sealer.open(c["current_value"], f"fact:{rid}")["v"])))
    return out


def kept_orphans(conn: Any, rid: str) -> set[str]:
    """Items a person kept, with a reason, although their document left the return (asked once, not every time)."""
    return {c["anchor"] for c in rows(conn, "SELECT anchor FROM fact_conflicts WHERE return_id = ? AND resolution = 'kept' "
                                            "AND anchor LIKE 'orphan:%'", rid)}


def close_conflict(conn: Any, conflict_id: int, *, resolution: str, actor: str, note: str) -> None:
    if resolution not in RESOLUTIONS:
        raise ValueError("resolution is 'kept', 'replaced' or 'superseded'")
    conn.execute("UPDATE fact_conflicts SET resolution = ?, resolved_by = ?, resolved_at = ?, note = ? WHERE id = ? AND resolved_at IS NULL",
                 (resolution, actor, audit.now(), note, conflict_id))


def locate(anchor: str, inputs: dict[str, Any], prov: dict[str, Any]) -> str | None:
    """The field an anchor names, in the current inputs: list items are found by their document only (never by a
    position that may have moved). None when the item is no longer on the return."""
    a = anchor.split(":", 1)[1] if anchor.startswith(("missing:", "orphan:", "duplicate:")) else anchor
    if "[" not in a:
        return a
    key, rest = a.split("[", 1)
    tag, _, field = rest.partition("]")
    field = field.lstrip(".")
    if tag.startswith("#"):
        return None                                                 # a hand-entered item has no stable identity
    for i, item in enumerate(inputs.get(key) or []):
        if item_doc(item, prov, key, i) == tag:
            return f"{key}[{i}].{field}" if field else f"{key}[{i}]"
    return None


def get_path(inputs: dict[str, Any], path: str) -> Any:
    if "[" in path:
        key, rest = path.split("[", 1)
        idx, _, field = rest.partition("]")
        items = inputs.get(key) or []
        i = int(idx)
        if i >= len(items):
            return None
        cur: Any = items[i]
        for part in [x for x in field.lstrip(".").split(".") if x]:
            cur = cur.get(part) if isinstance(cur, dict) else None
        return cur
    key, field = path.split(".", 1)
    return (inputs.get(key) or {}).get(field)


def set_path(inputs: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    out = copy.deepcopy(inputs)
    if "[" in path:
        key, rest = path.split("[", 1)
        idx, field = rest.split("].", 1)
        i = int(idx)
        if i >= len(out.get(key) or []):
            raise ValueError(f"{path} is no longer on the return")
        target = out[key][i]
        *parents, last = field.split(".")
        for part in parents:
            target = target.setdefault(part, {})
        target[last] = value
    else:
        key, field = path.split(".", 1)
        out.setdefault(key, {})[field] = value
    return out
