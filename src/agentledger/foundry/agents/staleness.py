"""Staleness Hunter: knows when each parameter is due, notices when one is late, goes looking.

Indexed parameters (inflation adjustments, mileage, wage base) need a new value every
year. The hunter computes when each is normally published, raises alerts as the date
approaches, and once it has passed uses a budgeted web search on official domains to
find the publication and push it through the RegWatch pipeline.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ...ai.router import Unavailable
from ...kb.store import KnowledgeBase
from ...regwatch import documents as docs
from ..core import AgentResult, AgentSpec, Foundry, agent_kind
from .regwatch import process


def scan(kb: KnowledgeBase, today: date, warn_days: int = 45, horizon_days: int = 1095) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rule in kb.rules.values():
        if rule.indexed:
            mm, dd = (int(x) for x in rule.indexed.expected_by.split("-"))
            for year in (today.year, today.year + 1):
                if rule.value_on(date(year, 12, 31)) is not None:
                    continue
                due = date(year - 1, mm, dd)
                if year == today.year:
                    status, sev = "missing", "critical"
                elif today > due:
                    status, sev = "overdue", "high"
                elif (due - today).days <= warn_days:
                    status, sev = "due_soon", "medium"
                else:
                    status, sev = "scheduled", "info"
                out.append({"rule_id": rule.id, "title": rule.title, "tax_year": year, "status": status, "severity": sev,
                            "expected_by": due.isoformat(), "days": (due - today).days,
                            "publication": rule.indexed.publication,
                            "search_hint": (rule.indexed.search_hint or f"{rule.title} {{year}}").format(year=year)})
        last = rule.latest()
        if not rule.indexed and last and last.effective_to and today <= last.effective_to <= today + timedelta(days=horizon_days):
            out.append({"rule_id": rule.id, "title": rule.title, "status": "sunset", "severity": "medium",
                        "expected_by": last.effective_to.isoformat(), "days": (last.effective_to - today).days,
                        "publication": "Statutory expiration; watch for extension legislation",
                        "search_hint": f"{rule.title} extension {last.effective_to.year}"})
    order = {"critical": 0, "high": 1, "medium": 2, "info": 3}
    return sorted(out, key=lambda a: (order[a["severity"]], a["days"]))


@agent_kind("staleness")
def staleness_agent(f: Foundry, spec: AgentSpec, res: AgentResult) -> None:
    pol = f.policy.get("staleness", {})
    alerts = scan(f.kb, date.today(), int(pol.get("warn_days_before_due", 45)), int(pol.get("sunset_horizon_days", 1095)))
    res.alerts.extend(a for a in alerts if a["severity"] != "info")
    res.stats["tracked"] = len(alerts)
    hunt = [a for a in alerts if a["status"] in ("missing", "overdue")]
    if not hunt or not spec.params.get("hunt", True):
        return
    if not f.router.frontier_allowed():
        res.log.append("overdue parameters found but frontier research is unavailable or out of budget")
        return
    official = f.policy.get("official_domains", [])
    asked: set[str] = set()
    for a in hunt[: int(spec.params.get("max_hunts", 3))]:
        q = a["search_hint"]
        if q in asked:
            continue
        asked.add(q)
        try:
            _, urls = f.router.frontier.web_research(
                f"Find the official publication that states: {q}. Prefer the primary document (PDF or Federal Register).",
                allowed_domains=official)
            f.router._log("frontier", f.router.frontier.model, "hunt", True)
        except Exception as e:
            res.log.append(f"hunt failed for {a['rule_id']}: {e}")
            continue
        found = [docs.Document(key=f"hunt:{u['url']}", source="staleness_hunt", title=u["title"], url=u["url"])
                 for u in urls[:4] if docs.is_official(u["url"], official)]
        res.log.append(f"{a['rule_id']} {a['tax_year'] if 'tax_year' in a else ''}: {len(found)} official candidate(s)")
        try:
            process(f, spec.id, found, res, max_docs=4)
        except Unavailable as e:
            res.log.append(str(e))
