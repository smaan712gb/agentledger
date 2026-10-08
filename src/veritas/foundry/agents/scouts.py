"""Scouts that keep the platform's own technology current.

Model Scout   - discovers newer open-source models (Ollama library, Hugging Face) and newer
                frontier models (Anthropic Models API); benchmarks challengers against each
                role's champion on our own eval suite; proposes promotion only on a real win.
Repo Scout    - maintains a live inventory of the open-source repositories the platform
                uses or could use: health, releases, licence, archival; discovers candidates.
Dependency    - compares installed package versions with PyPI and proposes upgrades.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

import httpx
import yaml

from ...ai.grounding import check_answer
from ...ai.local import LocalUnavailable
from ...ai.router import Unavailable
from ..core import AgentResult, AgentSpec, Check, Foundry, Proposal, agent_kind, applier

UA = {"User-Agent": "AgentLedger-Scout/0.1"}


# ============================================================================ evaluation suite

def load_evals(f: Foundry) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for p in sorted(f.paths.evals.glob("*.yaml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        out.setdefault(data["role"], []).extend(data.get("cases", []))
    return out


def score_local(f: Foundry, role: str, model: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Run the production prompts for a role against one local model. Returns score in [0, 1]."""
    from ...ask.engine import SYSTEM as ASK_SYSTEM
    from ...intake.classify import SYSTEM as CLS_SYSTEM, Classification
    from ...regwatch.draft import SYSTEM as DRAFT_SYSTEM, Draft

    total, valid, t0, details = 0.0, 0, time.time(), []
    for c in cases:
        try:
            if role == "classify":
                data, _ = f.router.local.chat_json(model, [{"role": "system", "content": CLS_SYSTEM},
                                                           {"role": "user", "content": c["input"]}], Classification.model_json_schema())
                r = Classification.model_validate(data)
                valid += 1
                fields = " ".join(kv.value for kv in r.fields)
                s = 0.6 * (r.doc_type == c["expect"]["doc_type"]) + 0.4 * (
                    sum(v in fields for v in c["expect"].get("values", [])) / max(1, len(c["expect"].get("values", []))))
            elif role in ("draft", "triage"):
                data, _ = f.router.local.chat_json(model, [{"role": "system", "content": DRAFT_SYSTEM},
                                                           {"role": "user", "content": c["input"]}], Draft.model_json_schema())
                r = Draft.model_validate(data)
                valid += 1
                if not c["expect"].get("relevant", True):
                    s = 1.0 if not r.changes else 0.0
                else:
                    want = {(x["rule_id"], json.dumps(x["value"])) for x in c["expect"]["changes"]}
                    got = set()
                    for ch in r.changes:
                        try:
                            got.add((ch.rule_id, json.dumps(json.loads(ch.value_json))))
                        except json.JSONDecodeError:
                            pass
                    s = len(want & got) / len(want)
            elif role == "answer":
                text = f.router.local.chat(model, [{"role": "system", "content": ASK_SYSTEM},
                                                   {"role": "user", "content": f"<evidence>\n{c['evidence']}\n</evidence>\n\nQuestion: {c['question']}"}],
                                           temperature=0.1)["text"]
                valid += 1
                ids: dict[str, set[str]] = {}
                for k, v in re.findall(r"\[(R|E|F|D|C|M|P):([^\]]+)\]", c["evidence"]):
                    ids.setdefault(k, set()).add(v.strip())
                v = check_answer(text, c["evidence"], ids)
                hits = sum(x.lower() in text.lower() for x in c.get("must_include", [])) / max(1, len(c.get("must_include", [])))
                s = 0.5 * v["grounded"] + 0.5 * hits
            else:
                continue
        except (LocalUnavailable, ValueError) as e:
            s = 0.0
            details.append(str(e)[:120])
        total += s
    n = max(1, len(cases))
    return {"score": round(total / n, 4), "json_valid": round(valid / n, 4), "seconds_per_case": round((time.time() - t0) / n, 2),
            "cases": len(cases), "errors": details[:5]}


# ============================================================================ model scout

def discover_open_models(trusted: list[str], limit: int = 25) -> list[dict[str, Any]]:
    out = []
    try:
        html = httpx.get("https://ollama.com/library?sort=newest", headers=UA, timeout=30).text
        for name in dict.fromkeys(re.findall(r'href="/library/([a-z0-9._-]+)"', html)):
            if any(name.startswith(t) for t in trusted):
                out.append({"source": "ollama", "name": name, "url": f"https://ollama.com/library/{name}"})
    except httpx.HTTPError:
        pass
    try:
        r = httpx.get("https://huggingface.co/api/models", params={"filter": "gguf", "sort": "trendingScore", "limit": 50},
                      headers=UA, timeout=30)
        for m in r.json():
            mid = m.get("id", "")
            if any(t in mid.lower() for t in trusted):
                out.append({"source": "huggingface", "name": f"hf.co/{mid}", "url": f"https://huggingface.co/{mid}",
                            "downloads": m.get("downloads"), "likes": m.get("likes")})
    except (httpx.HTTPError, ValueError):
        pass
    return out[:limit]


def discover_frontier(f: Foundry) -> list[dict[str, Any]]:
    if not f.router.frontier.available():
        return []
    try:
        models = list(f.router.frontier.client.models.list())
    except Exception:
        return []
    return [{"id": m.id, "created_at": str(getattr(m, "created_at", "")), "display_name": getattr(m, "display_name", m.id)}
            for m in models]


@agent_kind("model_scout")
def model_scout(f: Foundry, spec: AgentSpec, res: AgentResult) -> None:
    pol = f.policy.get("model_scout", {})
    margin = float(pol.get("margin", 0.03))
    trusted = pol.get("trusted_publishers", [])
    evals = load_evals(f)
    report: dict[str, Any] = {"at": datetime.now(timezone.utc).isoformat(), "roles": {}, "discovered": [], "frontier": []}

    # Full hosted open-model inventory (ADR-0009): Workers AI, NVIDIA, Hugging Face router and Hub, Ollama.
    if pol.get("inventory", True):
        from ...ai import inventory

        inv = inventory.refresh(f.paths.root / "state" / "model_inventory.json")
        report["inventory"] = inventory.summary(inv)
        res.stats["inventory"] = report["inventory"]
        for mid in inv["new"][: int(pol.get("max_new_alerts", 10))]:
            m = inv["models"][mid]
            cheapest = min((r for r in m["routes"] if r.get("price_in") is not None), default=None,
                           key=lambda r: r["price_in"] + (r["price_out"] or 0))
            res.alerts.append({"type": "model_listed", "model": mid, "hosts": sorted({r["host"] for r in m["routes"]}),
                               "cheapest": f"{cheapest['host']} ${cheapest['price_in']}/{cheapest['price_out']} per M tokens" if cheapest else None})

    discovered = discover_open_models(trusted)
    installed = {m["name"]: m for m in (f.router.local.models() if f.router.local.available() else [])}
    report["discovered"] = [{**d, "installed": d["name"] in installed or f"{d['name']}:latest" in installed} for d in discovered]
    new = [d for d in report["discovered"] if not d["installed"]][: int(pol.get("max_new_candidates", 3))]
    for d in new:
        res.alerts.append({"type": "model_available", "model": d["name"], "url": d["url"],
                           "note": "auto_pull disabled; pull it to let the scout benchmark it" if not pol.get("auto_pull") else "pulling"})
        if pol.get("auto_pull") and d["source"] == "ollama":
            try:
                f.router.local.pull(d["name"])
                installed[d["name"]] = {"name": d["name"], "size": 0}
            except LocalUnavailable as e:
                res.log.append(f"pull {d['name']} failed: {e}")

    max_bytes = float(pol.get("max_size_gb", 6)) * 1e9
    # Same weights can be installed under several names (aliases, raw digests): benchmark each
    # distinct model once, under its most human-readable name, so the registry stays reviewable.
    by_digest: dict[str, str] = {}
    for n, m in installed.items():
        if (m.get("size") or 0) > max_bytes or "embed" in n:
            continue
        key = m.get("digest") or n
        readable = lambda x: (re.search(r"[0-9a-f]{32}", x) is not None, x.startswith("llamacpp:"), len(x))
        if key not in by_digest or readable(n) < readable(by_digest[key]):
            by_digest[key] = n
    candidates = sorted(by_digest.values())
    for role, cases in evals.items():
        try:
            champ = f.router.registry.role(role)
        except Unavailable:
            continue
        if champ.tier != "local":
            continue
        scores = {}
        for model in dict.fromkeys([champ.model, *candidates]):
            if role == "vision" or not cases:
                continue
            scores[model] = score_local(f, role, model, cases)
            res.log.append(f"{role}: {model} -> {scores[model]['score']}")
        report["roles"][role] = {"champion": champ.model, "scores": scores}
        if champ.model not in scores:
            continue
        best = max(scores, key=lambda m: (scores[m]["score"], -scores[m]["seconds_per_case"]))
        if best != champ.model and scores[best]["score"] >= scores[champ.model]["score"] + margin:
            fp = f"{role}:{best}:{scores[best]['score']}"
            if f.duplicate_of("model_upgrade", fp):
                continue
            checks = [Check(name="beats_champion_by_margin", ok=True,
                            detail=f"{scores[best]['score']} vs {scores[champ.model]['score']} (margin {margin})"),
                      Check(name="json_validity", ok=scores[best]["json_valid"] >= 0.9, detail=str(scores[best]["json_valid"])),
                      Check(name="enough_cases", ok=scores[best]["cases"] >= 3, detail=str(scores[best]["cases"]))]
            p = Proposal(kind="model_upgrade", agent=spec.id, title=f"Promote {best} to '{role}' (was {champ.model})",
                         summary=f"On {scores[best]['cases']} evaluation cases, {best} scored {scores[best]['score']} vs the "
                                 f"current champion's {scores[champ.model]['score']}. Reversible in one click.",
                         risk="medium", risk_reasons=["model swap: reversible, verified on our eval suite"],
                         payload={"role": role, "tier": "local", "model": best, "fingerprint": fp, "scores": scores},
                         checks=checks)
            res.proposals.append(f.submit(p).id)

    report["frontier"] = discover_frontier(f)
    for role in ("reason", "code"):
        try:
            champ = f.router.registry.role(role)
        except Unavailable:
            continue
        ids = [m["id"] for m in report["frontier"]]
        if champ.tier == "frontier" and ids and champ.model in ids:
            newer = ids[: ids.index(champ.model)]  # Models API lists newest first
            if newer:
                res.alerts.append({"type": "frontier_model_available", "role": role, "current": champ.model, "newer": newer[:3],
                                   "note": "Run the 'reason' evals with budget to evaluate, or approve a swap."})
    (f.paths.state / "model_scout_report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")


@applier("model_upgrade", undo=lambda f, p: f.router.registry.set_role(
    p.payload["role"], p.undo["previous"]["tier"], p.undo["previous"]["model"], f"rollback of {p.id}"))
def apply_model_upgrade(f: Foundry, p: Proposal) -> dict[str, Any]:
    prev = f.router.registry.data["roles"].get(p.payload["role"])
    f.router.registry.set_role(p.payload["role"], p.payload["tier"], p.payload["model"], f"proposal {p.id}",
                               {"scores": p.payload.get("scores")})
    return {"previous": {"tier": prev["tier"], "model": prev["model"]}}


# ============================================================================ repo scout

def _gh(url: str, params: dict[str, Any] | None = None) -> Any:
    import os

    headers = {**UA, "Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    r = httpx.get(f"https://api.github.com{url}", params=params, headers=headers, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


@agent_kind("repo_scout")
def repo_scout(f: Foundry, spec: AgentSpec, res: AgentResult) -> None:
    inv = yaml.safe_load((f.paths.config / "oss_inventory.yaml").read_text(encoding="utf-8"))
    known = {r["repo"].lower() for r in inv["repos"]}
    stale_after = timedelta(days=int(inv.get("stale_after_days", 365)))
    report: dict[str, Any] = {"at": datetime.now(timezone.utc).isoformat(), "repos": [], "candidates": []}
    for r in inv["repos"]:
        try:
            meta = _gh(f"/repos/{r['repo']}")
            if meta is None:
                res.alerts.append({"type": "repo_missing", "repo": r["repo"], "severity": "high"})
                continue
            rel = _gh(f"/repos/{r['repo']}/releases/latest")
        except httpx.HTTPError as e:
            res.log.append(f"{r['repo']}: {e}")
            break  # most likely rate limited; keep what we have
        pushed = datetime.fromisoformat(meta["pushed_at"].replace("Z", "+00:00"))
        health = "archived" if meta.get("archived") else "stale" if datetime.now(timezone.utc) - pushed > stale_after else "active"
        row = {"repo": r["repo"], "role": r["role"], "used": r.get("used", False), "stars": meta.get("stargazers_count"),
               "license": (meta.get("license") or {}).get("spdx_id"), "pushed_at": meta["pushed_at"], "health": health,
               "latest_release": rel.get("tag_name") if rel else None, "release_date": rel.get("published_at") if rel else None}
        report["repos"].append(row)
        if r.get("used") and health != "active":
            res.alerts.append({"type": "dependency_health", "repo": r["repo"], "health": health, "severity": "high"})
        if r.get("license") and row["license"] and row["license"] != r["license"]:
            res.alerts.append({"type": "license_changed", "repo": r["repo"], "from": r["license"], "to": row["license"],
                               "severity": "critical"})
    since = (date.today() - timedelta(days=180)).isoformat()
    for q in inv.get("discovery_queries", []):
        try:
            data = _gh("/search/repositories", {"q": f"{q} pushed:>{since}", "sort": "stars", "per_page": 5})
        except httpx.HTTPError:
            break
        for item in (data or {}).get("items", []):
            name = item["full_name"].lower()
            if name in known or item.get("stargazers_count", 0) < int(inv.get("min_stars", 500)):
                continue
            known.add(name)
            cand = {"repo": item["full_name"], "stars": item["stargazers_count"], "description": item.get("description"),
                    "license": (item.get("license") or {}).get("spdx_id"), "query": q, "url": item["html_url"]}
            report["candidates"].append(cand)
            allowed = cand["license"] in inv.get("allowed_licenses", [])
            fp = f"repo:{name}"
            if allowed and not f.duplicate_of("repo_adoption", fp):
                p = Proposal(kind="repo_adoption", agent=spec.id, title=f"Evaluate {item['full_name']} ({cand['stars']}★)",
                             summary=f"{cand['description'] or ''} Found for '{q}'. Adding it to the inventory lets the scouts "
                                     "track it; adopting it in code is a separate, human-approved engineering change.",
                             risk="high", risk_reasons=["supply chain: always human-reviewed"],
                             payload={**cand, "fingerprint": fp},
                             checks=[Check(name="license_allowed", ok=allowed, detail=str(cand["license"]))])
                res.proposals.append(f.submit(p).id)
    (f.paths.state / "oss_inventory_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    res.stats.update(repos=len(report["repos"]), candidates=len(report["candidates"]))


@applier("repo_adoption")
def apply_repo_adoption(f: Foundry, p: Proposal) -> dict[str, Any]:
    path = f.paths.config / "oss_inventory.yaml"
    inv = yaml.safe_load(path.read_text(encoding="utf-8"))
    inv["repos"].append({"repo": p.payload["repo"], "role": f"candidate ({p.payload.get('query')})", "used": False,
                         "license": p.payload.get("license")})
    path.write_text(yaml.safe_dump(inv, sort_keys=False, width=110), encoding="utf-8")
    return {"added": p.payload["repo"]}


# ============================================================================ dependency watch

@agent_kind("dependency_watch")
def dependency_watch(f: Foundry, spec: AgentSpec, res: AgentResult) -> None:
    import tomllib

    py = tomllib.loads((f.paths.root / "pyproject.toml").read_text(encoding="utf-8"))
    names = [re.split(r"[<>=\[ ]", d, maxsplit=1)[0] for d in py["project"]["dependencies"]]
    for name in names + spec.params.get("extra", []):
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            installed = None
        try:
            latest = httpx.get(f"https://pypi.org/pypi/{name}/json", headers=UA, timeout=20).json()["info"]["version"]
        except (httpx.HTTPError, KeyError, ValueError):
            continue
        if installed and latest != installed:
            major = installed.split(".")[0] != latest.split(".")[0]
            fp = f"dep:{name}:{latest}"
            if f.duplicate_of("dependency_upgrade", fp):
                continue
            p = Proposal(kind="dependency_upgrade", agent=spec.id, title=f"Upgrade {name} {installed} → {latest}",
                         summary=f"{'Major' if major else 'Minor/patch'} release on PyPI. If approved, the AI Engineer applies it in an "
                                 "isolated worktree and must pass the full test and golden suites before it can merge.",
                         risk="high" if major else "medium",
                         risk_reasons=["supply chain: always human-approved", *(["major version"] if major else [])],
                         payload={"package": name, "from": installed, "to": latest, "fingerprint": fp},
                         checks=[Check(name="release_exists_on_pypi", ok=True, detail=latest)])
            res.proposals.append(f.submit(p).id)


@applier("dependency_upgrade")
def apply_dependency_upgrade(f: Foundry, p: Proposal) -> dict[str, Any]:
    """Approval turns into an engineering work item; the Engineer does the change under its gates."""
    work = f.paths.state / "work"
    work.mkdir(parents=True, exist_ok=True)
    item = {"title": p.title, "items": [f"Upgrade Python dependency {p.payload['package']} to {p.payload['to']} in pyproject.toml; "
                                        "fix any breakage; keep all tests green."], "status": "open", "source": f"proposal {p.id}"}
    (work / f"dep_{p.payload['package']}_{p.payload['to']}.json").write_text(json.dumps(item, indent=2), encoding="utf-8")
    return {"work_item": True}
