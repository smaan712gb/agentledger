"""Built-in connectors, importers and exporters (all run inside a PluginContext)."""

from __future__ import annotations

import csv
import io
import os
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .registry import PluginContext


def bank_csv(ctx: PluginContext) -> int:
    from ..ledger.bankfeed import parse_csv

    return len(ctx.suggest_transactions(parse_csv(ctx.config["text"])))


def ofx(ctx: PluginContext) -> int:
    text = ctx.config["text"]
    txns = []
    for block in re.findall(r"<STMTTRN>(.*?)(?:</STMTTRN>|(?=<STMTTRN>)|(?=</BANKTRANLIST>))", text, re.S | re.I):
        def tag(name: str) -> str:
            m = re.search(rf"<{name}>([^<\r\n]+)", block, re.I)
            return m.group(1).strip() if m else ""

        posted, amount = tag("DTPOSTED")[:8], tag("TRNAMT")
        if not (posted and amount):
            continue
        txns.append({"date": datetime.strptime(posted, "%Y%m%d").date().isoformat(),
                     "description": " ".join(filter(None, [tag("NAME"), tag("MEMO")])) or "OFX transaction",
                     "amount": str(Decimal(amount))})
    return len(ctx.suggest_transactions(txns))


def folder_watch(ctx: PluginContext) -> int:
    folder = Path(ctx.config["path"]).expanduser()
    done = folder / ".veritas-processed"
    done.mkdir(exist_ok=True)
    n = 0
    for p in sorted(x for x in folder.iterdir() if x.is_file() and not x.name.startswith(".")):
        ctx.ingest_document(p.name, p.read_bytes())
        p.replace(done / p.name)
        n += 1
    return n


def stripe(ctx: PluginContext) -> int:
    key = os.environ.get(ctx.config.get("api_key_env", "STRIPE_API_KEY"))
    if not key:
        raise RuntimeError("Stripe key env var not set")
    r = ctx.http_get("https://api.stripe.com/v1/balance_transactions", params={"limit": 100}, auth=(key, ""))
    r.raise_for_status()
    txns = [{"date": date.fromtimestamp(t["created"]).isoformat(),
             "description": f"STRIPE {t['type'].upper()} {t.get('description') or ''}".strip(),
             "amount": str(Decimal(t["amount"]) / 100)} for t in r.json().get("data", [])]
    return len(ctx.suggest_transactions(txns))


def trial_balance_import(ctx: PluginContext) -> dict[str, Any]:
    """Opening balances from a QuickBooks/Xero trial balance. Unmapped accounts are reported, not guessed."""
    accounts = ctx.ledger().accounts()
    by_name = {a["name"].lower(): code for code, a in accounts.items()}
    lines, unmapped = [], []
    for r in csv.DictReader(io.StringIO(ctx.config["text"].strip())):
        name = (r.get("Account") or r.get("account") or "").strip()
        if not name or name.lower().startswith("total"):
            continue
        debit = Decimal(re.sub(r"[^\d.\-]", "", r.get("Debit") or r.get("debit") or "") or "0")
        credit = Decimal(re.sub(r"[^\d.\-]", "", r.get("Credit") or r.get("credit") or "") or "0")
        code = by_name.get(name.lower()) or next((c for n, c in by_name.items() if n in name.lower() or name.lower() in n), None)
        if code is None:
            unmapped.append(name)
            continue
        if debit - credit:
            lines.append((code, debit - credit))
    if unmapped:
        return {"posted": False, "unmapped_accounts": unmapped, "message": "Map these accounts (or add them) and re-run."}
    as_of = date.fromisoformat(ctx.config.get("as_of") or date(date.today().year, 1, 1).isoformat())
    entry = ctx.post_opening_balances(lines, as_of)
    return {"posted": True, "entry_id": entry, "lines": len(lines)}


def iif_export(ctx: PluginContext) -> str:
    led = ctx.ledger()
    acc = led.accounts()
    out = ["!TRNS\tTRNSID\tTRNSTYPE\tDATE\tACCNT\tAMOUNT\tMEMO", "!SPL\tSPLID\tTRNSTYPE\tDATE\tACCNT\tAMOUNT\tMEMO", "!ENDTRNS"]
    for e in led.entries():
        d = date.fromisoformat(e["date"]).strftime("%m/%d/%Y")
        memo = e["memo"].replace("\t", " ")
        for i, p in enumerate(e["postings"]):
            tag = "TRNS" if i == 0 else "SPL"
            out.append(f"{tag}\t\tGENERAL JOURNAL\t{d}\t{acc[p['account_code']]['name']}\t{Decimal(p['amount']):.2f}\t{memo}")
        out.append("ENDTRNS")
    return "\n".join(out) + "\n"


def tax_tb_export(ctx: PluginContext) -> str:
    from ..calc.engine import Ctx
    from ..ledger import m1

    year = int(ctx.config.get("tax_year") or date.today().year - 1)
    bal = ctx.ledger().balances(date(year, 1, 1), date(year, 12, 31))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Account", "Name", "Type", "Debit", "Credit"])
    for b in bal:
        v = Decimal(b["balance"])
        w.writerow([b["code"], b["name"], b["type"], f"{v:.2f}" if v > 0 else "", f"{-v:.2f}" if v < 0 else ""])
    w.writerow([])
    w.writerow(["Schedule M-1", "Line", "Label", "Amount", ""])
    for l in m1.compute(ctx.conn, Ctx(ctx.foundry.kb), ctx.client_id, year).lines:
        w.writerow(["M-1", l.line, l.label, f"{l.amount:.2f}", ""])
    return buf.getvalue()


def beancount_export(ctx: PluginContext) -> str:
    return ctx.ledger().beancount()


def mcp_bridge(ctx: PluginContext) -> dict[str, Any]:
    """Pull from any external MCP server (QuickBooks, Gmail, Drive, a bank...) and normalize.

    config: command, args, tool, arguments, produces ("transactions" | "documents").
    The external tool must return JSON: a list of {date, description, amount} for
    transactions, or of {filename, content_base64} for documents.
    """
    import anyio
    import base64
    import json as _json

    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    ctx._need("network")
    params = StdioServerParameters(command=ctx.config["command"], args=list(ctx.config.get("args", [])))

    async def call() -> Any:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool(ctx.config["tool"], dict(ctx.config.get("arguments", {})))
                structured = getattr(res, "structuredContent", None) or getattr(res, "structured_content", None)
                if structured is not None:
                    return structured.get("result", structured) if isinstance(structured, dict) else structured
                text = "".join(getattr(c, "text", "") for c in getattr(res, "content", []))
                return _json.loads(text)

    items = anyio.run(call)
    if ctx.config.get("produces", "transactions") == "documents":
        n = 0
        for d in items:
            ctx.ingest_document(d["filename"], base64.b64decode(d["content_base64"]))
            n += 1
        return {"documents": n}
    return {"transactions": len(ctx.suggest_transactions(items))}


def webhook_inbound(ctx: PluginContext) -> dict[str, Any]:
    """Universal inbound webhook (Zapier, Make, n8n, any SaaS). Payload already HMAC-verified by the API.

    Accepts {"transactions": [...]} and/or {"documents": [{"filename", "content_base64"}]}.
    """
    import base64

    body = ctx.config["payload"]
    out = {}
    if body.get("transactions"):
        out["transactions"] = len(ctx.suggest_transactions(body["transactions"]))
    for d in body.get("documents", []):
        ctx.ingest_document(d["filename"], base64.b64decode(d["content_base64"]), sender=body.get("sender"))
        out["documents"] = out.get("documents", 0) + 1
    return out
