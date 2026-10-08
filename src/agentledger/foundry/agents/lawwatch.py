"""Agents that keep AgentLedger current with every state and with the IRS (ADR-0010).

- state_watch:        each state tax agency's news and guidance pages, in rotating batches
- legislation_watch:  enacted tax laws in every state (Open States) and in Congress (GovInfo)
- irs_forms_watch:    new drafts and revisions of every IRS form and instruction we compute

Every finding goes through the same pipeline as federal RegWatch (draft -> Sentinel verification -> proposal).
Anything that could make an existing calculation wrong also raises a coverage flag. Flags block filing for that
jurisdiction and year until the change is implemented, tested and cleared by the tax-content owner.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import date, datetime, timezone
from typing import Any

import httpx
import yaml

from ... import coverage
from ...regwatch import states as st
from ..core import AgentResult, AgentSpec, Foundry, agent_kind
from .regwatch import process, queue_work


def _state(f: Foundry, name: str) -> dict[str, Any]:
    p = f.paths.state / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _save(f: Foundry, name: str, data: dict[str, Any]) -> None:
    p = f.paths.state / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@agent_kind("state_watch")
def state_watch_agent(f: Foundry, spec: AgentSpec, res: AgentResult) -> None:
    p = spec.params
    batch = int(p.get("batch", 8))
    mem = _state(f, "state_watch.json")
    jurisdictions = st.load_jurisdictions(f.paths.root)
    official = sorted(set(f.policy.get("official_domains", [])) | set(st.state_domains(f.paths.root)))
    due = sorted(jurisdictions, key=lambda j: mem.get(j["code"], {}).get("last_checked", ""))[:batch]
    for j in due:
        rec = mem.setdefault(j["code"], {})
        agency = st.primary(j)
        if not agency:
            res.log.append(f"{j['code']}: no primary agency in the registry")
            continue
        try:
            manual = [u for u in ((j.get("watch") or {}).get("news_url"), (j.get("watch") or {}).get("rss")) if u]
            if manual:
                rec["pages"] = manual           # a human-set page in the registry wins over discovery
            elif not rec.get("pages"):
                rec["pages"] = st.discover_news_pages(agency["url"])
                rec["discovered_at"] = _now()
                if not rec["pages"]:
                    res.alerts.append({"type": "state_watch_no_page", "state": j["code"], "agency": agency["name"],
                                       "note": "no news page found on the agency site; add one to the registry"})
            for page in rec.get("pages", []):
                process(f, spec.id, st.page_items(page, f"state-{j['code'].lower()}"), res,
                        max_docs=int(p.get("max_docs_per_state", 3)), official_extra=official)
            rec["last_checked"], rec["error"] = _now(), None
        except Exception as e:  # one state's outage must not stop the others
            rec["last_checked"], rec["error"] = _now(), f"{type(e).__name__}: {e}"[:300]
            res.log.append(f"{j['code']}: {rec['error']}")
    _save(f, "state_watch.json", mem)
    res.stats["states_checked"] = [j["code"] for j in due]
    res.stats["states_watched"] = sum(1 for v in mem.values() if v.get("pages"))


@agent_kind("legislation_watch")
def legislation_watch_agent(f: Foundry, spec: AgentSpec, res: AgentResult) -> None:
    p = spec.params
    since = st.lookback(int(p.get("lookback_days", 14)))
    official = sorted(set(f.policy.get("official_domains", [])) | set(st.state_domains(f.paths.root)) |
                      {"govinfo.gov", "congress.gov", "openstates.org"})
    sources = []
    if p.get("federal", True):
        sources.append(("US-FED", lambda: st.govinfo_public_laws(since)))
    codes = p.get("states") or [j["code"] for j in st.load_jurisdictions(f.paths.root)]
    sources += [(f"US-{c}", (lambda c=c: st.openstates_enacted(c, since))) for c in codes]
    unavailable = set()
    for jur, fn in sources:
        try:
            laws = list(fn())
        except RuntimeError as e:  # missing API key: report once, do not pretend we looked
            unavailable.add(str(e))
            continue
        except Exception as e:
            res.log.append(f"{jur}: {type(e).__name__}: {e}"[:200])
            continue
        for doc in laws:
            years = _years_from(doc)
            flag = coverage.flag(f.paths.root, jurisdiction=jur, years=years, reason=f"enacted: {doc.title}", source=doc.url,
                                 raised_by=spec.id)
            if flag["new"]:
                res.alerts.append({"type": "law_enacted", "jurisdiction": jur, "title": doc.title, "url": doc.url,
                                   "years": years, "coverage_flag": flag["id"]})
            process(f, spec.id, [doc], res, max_docs=int(p.get("max_docs", 10)), official_extra=official)
    for msg in sorted(unavailable):
        res.alerts.append({"type": "source_unavailable", "detail": msg})
    res.stats["jurisdictions"] = len(sources)


def _years_from(doc) -> list[int]:
    """Tax years a law may touch: the year enacted and the next, unless the title names years."""
    named = sorted({int(y) for y in re.findall(r"\b(20[2-4]\d)\b", doc.title)})
    if named:
        return named
    y = int((doc.published or date.today().isoformat())[:4])
    return [y, y + 1]


@agent_kind("irs_forms_watch")
def irs_forms_watch_agent(f: Foundry, spec: AgentSpec, res: AgentResult) -> None:
    """Detect new or revised IRS drafts and final forms for every form the engine computes."""
    cfg = yaml.safe_load((f.paths.config / "irs_forms.yaml").read_text(encoding="utf-8"))
    mem = _state(f, "irs_forms_seen.json")
    changed = []
    for form_id, files in cfg["forms"].items():
        for name in files:
            for kind, base in (("draft", "https://www.irs.gov/pub/irs-dft/"), ("final", "https://www.irs.gov/pub/irs-pdf/")):
                fname = name.replace(".pdf", "--dft.pdf") if kind == "draft" else name
                url = base + fname
                try:
                    r = httpx.get(url, timeout=60, follow_redirects=True, headers=st.BROWSER_UA)
                except httpx.HTTPError as e:
                    res.log.append(f"{url}: {e}")
                    continue
                key = f"{kind}:{fname}"
                if r.status_code != 200 or not r.content.startswith(b"%PDF"):
                    if key in mem and mem[key].get("present"):
                        mem[key]["present"] = False
                        res.log.append(f"{fname}: no longer published as {kind}")
                    continue
                digest = hashlib.sha256(r.content).hexdigest()
                prev = mem.get(key)
                if prev and prev.get("sha256") == digest:
                    continue
                year = _form_year(r.content)
                mem[key] = {"sha256": digest, "seen_at": _now(), "year": year, "present": True, "url": url}
                if prev is None and not p_initial(spec):
                    continue  # first sighting during the initial baseline: record without alarm
                changed.append({"form": form_id, "file": fname, "kind": kind, "year": year, "url": url})
    _save(f, "irs_forms_seen.json", mem)
    for c in changed:
        flag = coverage.flag(f.paths.root, jurisdiction="US-FED", years=[c["year"]] if c["year"] else [], form=c["form"],
                             reason=f"IRS {c['kind']} {c['file']} {'published' if c['kind'] == 'draft' else 'revised'}",
                             source=c["url"], raised_by=spec.id)
        res.alerts.append({"type": "irs_form_changed", **c, "coverage_flag": flag["id"]})
        doc = type("D", (), {"url": c["url"], "title": f"IRS {c['file']}", "key": f"irsform:{c['file']}"})()
        queue_work(f, doc, [f"Form {c['form']} ({c['file']}, {c['kind']}, TY{c['year']}) changed: diff its lines against the "
                            f"engine's line map, update the computation and the per-year field map, add fixtures from the "
                            f"instructions' examples, run the full suite, golden and PolicyEngine checks."])
    res.stats["changed"] = len(changed)


def p_initial(spec: AgentSpec) -> bool:
    return bool(spec.params.get("alert_on_first_sighting", False))


def _form_year(pdf: bytes) -> int | None:
    try:
        import pypdf

        text = pypdf.PdfReader(io.BytesIO(pdf)).pages[0].extract_text() or ""
    except Exception:
        return None
    m = re.search(r"\b(20[2-4]\d)\b", text[:600])
    return int(m.group(1)) if m else None
