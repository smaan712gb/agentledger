"""Live inventory of open models we could route to (ADR-0009).

Sources, merged into one list of models, each with every hosting route we can reach:
- Cloudflare Workers AI catalog (in-platform; needs CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN)
- NVIDIA API catalog (integrate.api.nvidia.com)
- Hugging Face Inference Providers router (per-provider price, context, latency, throughput)
- Hugging Face Hub trending text-generation models (discovery only)
- Ollama library (local and self-hosted)

The inventory is data for the Model Scout: candidates still have to win on our own evals before any
role uses them, and the data-class policy (ADR-0007) decides which routes may see taxpayer data.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

UA = {"User-Agent": "AgentLedger-ModelScout/1.0"}
TIMEOUT = 30


@dataclass
class Route:
    host: str                       # workers-ai | nvidia | hf:<provider> | ollama | hf-hub
    model_ref: str                  # id to send to that host
    price_in: float | None = None   # USD per million input tokens
    price_out: float | None = None
    context: int | None = None
    structured_output: bool | None = None
    tools: bool | None = None
    first_token_ms: float | None = None
    throughput_tps: float | None = None
    in_platform: bool = False       # runs inside our own infrastructure provider (no third-party subprocessor)


@dataclass
class Model:
    id: str                         # normalised family/name, lower case
    names: list[str] = field(default_factory=list)
    owner: str = ""
    modalities: list[str] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)
    first_seen: str = ""
    last_seen: str = ""

    @property
    def cheapest(self) -> Route | None:
        priced = [r for r in self.routes if r.price_in is not None and r.price_out is not None]
        return min(priced, key=lambda r: r.price_in + r.price_out) if priced else None


def norm(name: str) -> str:
    """Normalise ids across hosts: '@cf/qwen/qwen3.8-27b', 'Qwen/Qwen3.8-27B' and 'qwen/qwen3.8-27b' are one model."""
    n = name.strip().lower().removeprefix("@cf/").removeprefix("@hf/").removeprefix("hf.co/")
    n = re.sub(r"[-_](fp8|fp16|bf16|awq|int4|int8|nvfp4|gguf)(-fast)?$", "", n)
    return n


def _get(url: str, **kw) -> Any:
    r = httpx.get(url, headers={**UA, **kw.pop("headers", {})}, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r.json()


def from_hf_router() -> list[tuple[str, Model]]:
    out = []
    for m in _get("https://router.huggingface.co/v1/models").get("data", []):
        model = Model(id=norm(m["id"]), names=[m["id"]], owner=m.get("owned_by", ""),
                      modalities=sorted(set(m.get("architecture", {}).get("input_modalities", []))))
        for p in m.get("providers", []):
            if p.get("status") not in (None, "live"):
                continue
            pr = p.get("pricing") or {}
            model.routes.append(Route(host=f"hf:{p['provider']}", model_ref=f"{m['id']}:{p['provider']}",
                                      price_in=pr.get("input"), price_out=pr.get("output"), context=p.get("context_length"),
                                      structured_output=p.get("supports_structured_output"), tools=p.get("supports_tools"),
                                      first_token_ms=p.get("first_token_latency_ms"), throughput_tps=p.get("throughput")))
        out.append(("hf-router", model))
    return out


def from_nvidia() -> list[tuple[str, Model]]:
    return [("nvidia", Model(id=norm(m["id"]), names=[m["id"]], owner=m.get("owned_by", ""),
                             routes=[Route(host="nvidia", model_ref=m["id"])]))
            for m in _get("https://integrate.api.nvidia.com/v1/models").get("data", [])]


def from_workers_ai() -> list[tuple[str, Model]]:
    account, token = os.environ.get("CLOUDFLARE_ACCOUNT_ID"), os.environ.get("CLOUDFLARE_API_TOKEN")
    if not account or not token:
        raise RuntimeError("CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN not set")
    out, page = [], 1
    while True:
        d = _get(f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/models/search",
                 params={"per_page": 100, "page": page}, headers={"Authorization": f"Bearer {token}"})
        for m in d.get("result", []):
            task = (m.get("task") or {}).get("name", "")
            props = {p.get("property_id"): p.get("value") for p in m.get("properties", [])}
            ctx = props.get("context_window")
            out.append(("workers-ai", Model(id=norm(m["name"]), names=[m["name"]], owner=m["name"].split("/")[1] if "/" in m["name"] else "",
                                            modalities=[task] if task else [],
                                            routes=[Route(host="workers-ai", model_ref=m["name"], in_platform=True,
                                                          context=int(ctx) if str(ctx or "").isdigit() else None)])))
        if len(d.get("result", [])) < 100:
            break
        page += 1
    return out


def from_hf_hub(limit: int = 60) -> list[tuple[str, Model]]:
    d = _get("https://huggingface.co/api/models", params={"pipeline_tag": "text-generation", "sort": "trendingScore", "limit": limit})
    return [("hf-hub", Model(id=norm(m["id"]), names=[m["id"]], owner=m["id"].split("/")[0],
                             routes=[Route(host="hf-hub", model_ref=m["id"])])) for m in d]


def from_ollama(limit: int = 40) -> list[tuple[str, Model]]:
    html = httpx.get("https://ollama.com/library?sort=newest", headers=UA, timeout=TIMEOUT).text
    names = list(dict.fromkeys(re.findall(r'href="/library/([a-z0-9][a-z0-9._-]*)"', html)))[:limit]
    return [("ollama", Model(id=norm(n), names=[n], routes=[Route(host="ollama", model_ref=n)])) for n in names]


SOURCES: dict[str, Callable[[], list[tuple[str, Model]]]] = {
    "workers-ai": from_workers_ai, "nvidia": from_nvidia, "hf-router": from_hf_router, "hf-hub": from_hf_hub, "ollama": from_ollama,
}


def refresh(path: Path, sources: dict[str, Callable] | None = None) -> dict[str, Any]:
    """Fetch every source, merge by normalised id, and diff against the previous inventory at `path`."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    prev = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"models": {}}
    merged: dict[str, Model] = {}
    status: dict[str, str] = {}
    for name, fn in (sources or SOURCES).items():
        try:
            rows = fn()
            status[name] = f"ok ({len(rows)})"
        except Exception as e:  # one source down must not empty the inventory
            status[name] = f"unavailable: {type(e).__name__}: {e}"[:200]
            continue
        for _, m in rows:
            cur = merged.setdefault(m.id, Model(id=m.id, owner=m.owner))
            cur.names = sorted(set(cur.names) | set(m.names))
            cur.modalities = sorted(set(cur.modalities) | set(m.modalities))
            cur.owner = cur.owner or m.owner
            seen = {(r.host, r.model_ref) for r in cur.routes}
            cur.routes += [r for r in m.routes if (r.host, r.model_ref) not in seen]
    models = {}
    for mid, m in merged.items():
        old = prev["models"].get(mid)
        m.first_seen = old["first_seen"] if old else now
        m.last_seen = now
        models[mid] = asdict(m)
    ok_sources = {k for k, v in status.items() if v.startswith("ok")}
    retired = []
    for mid, old in prev["models"].items():
        if mid in models:
            continue
        # Only retire a model when every source that listed it answered this time.
        hosts = {r["host"].split(":")[0] for r in old.get("routes", [])}
        src_of = {"workers-ai": "workers-ai", "nvidia": "nvidia", "hf": "hf-router", "hf-hub": "hf-hub", "ollama": "ollama"}
        if {src_of.get(h, h) for h in hosts} <= ok_sources:
            retired.append(mid)
        else:
            models[mid] = old
    new = sorted(set(models) - set(prev["models"]))
    inv = {"refreshed_at": now, "sources": status, "models": models, "new": new, "retired": sorted(retired)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(inv, indent=1), encoding="utf-8")
    return inv


def summary(inv: dict[str, Any]) -> dict[str, Any]:
    routes = [r for m in inv["models"].values() for r in m["routes"]]
    hosts: dict[str, int] = {}
    for r in routes:
        h = r["host"].split(":")[0] if r["host"].startswith("hf:") else r["host"]
        hosts[h] = hosts.get(h, 0) + 1
    deployable = [m for m in inv["models"].values() if any(r["host"] not in ("hf-hub",) for r in m["routes"])]
    return {"models": len(inv["models"]), "deployable": len(deployable), "routes_by_host": hosts,
            "new": len(inv.get("new", [])), "retired": len(inv.get("retired", [])), "sources": inv["sources"]}
