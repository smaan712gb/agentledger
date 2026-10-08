"""Domain packs: industries as data, not code.

A pack declares a chart of accounts, posting templates (balanced journal-entry
recipes evaluated by the safe expression engine), domain document types, KPIs and
declarative integrity rules. Templates may pull statutory values from the KB
("rules:"), so a regulation change flows into industry postings automatically.
New packs are drafted by the Domain Architect agent and verified mechanically.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

from ..expr import ExprError, evaluate, names_used
from ..kb.store import KnowledgeBase
from ..ledger.store import TREATMENTS


class AccountDef(BaseModel):
    code: str
    name: str
    type: Literal["asset", "liability", "equity", "revenue", "expense"]


class TemplateInput(BaseModel):
    name: str
    type: Literal["money", "number", "text", "account"] = "money"
    description: str = ""


class TemplateLine(BaseModel):
    account: str  # account code, or "$input_name" for an account chosen at posting time
    amount: str  # expression; positive = debit
    tax_treatment: str | None = None
    when: str | None = None  # optional condition expression


class Template(BaseModel):
    id: str
    title: str
    description: str = ""
    memo: str
    inputs: list[TemplateInput]
    rules: dict[str, str] = {}  # name -> "rule_id" or "rule_id:key"
    lines: list[TemplateLine]
    sample: dict[str, Any]


class DomainCheck(BaseModel):
    id: str
    title: str
    type: Literal["min_balance", "ratio_max", "memo_pattern", "memo_account", "coverage"]
    params: dict[str, Any]
    severity: Literal["info", "low", "medium", "high", "critical"] = "medium"
    owner: Literal["client", "cpa", "both"] = "cpa"
    citation: str | None = None
    detail: str = ""


class KPI(BaseModel):
    id: str
    title: str
    expr: str  # over bal_<code> (normal-balance positive) and client facts
    unit: str = ""


class DomainPack(BaseModel):
    id: str
    title: str
    description: str
    extends: str | None = "general"
    accounts: list[AccountDef] = []
    templates: list[Template] = []
    doc_types: list[str] = []
    facts: list[str] = []  # client facts this domain cares about
    checks: list[DomainCheck] = []
    kpis: list[KPI] = []
    status: Literal["active", "draft"] = "active"


class Packs:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.packs: dict[str, DomainPack] = {}
        for p in sorted(self.root.glob("*.yaml")):
            pack = DomainPack.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
            self.packs[pack.id] = pack

    def get(self, pack_id: str) -> DomainPack:
        if pack_id not in self.packs:
            raise KeyError(f"unknown domain pack {pack_id}")
        return self.packs[pack_id]

    def chain(self, pack_id: str) -> list[DomainPack]:
        out, seen = [], set()
        cur: str | None = pack_id
        while cur and cur not in seen:
            seen.add(cur)
            pack = self.get(cur)
            out.append(pack)
            cur = pack.extends if pack.id != "general" else None
        return list(reversed(out))

    def accounts(self, pack_id: str) -> dict[str, AccountDef]:
        acc: dict[str, AccountDef] = {}
        for p in self.chain(pack_id):
            acc.update({a.code: a for a in p.accounts})
        return acc

    def templates(self, pack_id: str) -> dict[str, Template]:
        t: dict[str, Template] = {}
        for p in self.chain(pack_id):
            t.update({x.id: x for x in p.templates})
        return t

    def checks(self, pack_id: str) -> list[DomainCheck]:
        return [c for p in self.chain(pack_id) for c in p.checks]

    def kpis(self, pack_id: str) -> list[KPI]:
        """The most specific pack's KPIs: generic ones mislead when an industry uses its own accounts."""
        for p in reversed(self.chain(pack_id)):
            if p.kpis:
                return list(p.kpis)
        return []

    def save(self, pack: DomainPack) -> Path:
        path = self.root / f"{pack.id}.yaml"
        path.write_text(yaml.safe_dump(pack.model_dump(exclude_defaults=False), sort_keys=False, width=110), encoding="utf-8")
        self.packs[pack.id] = pack
        return path


def render(t: Template, inputs: dict[str, Any], kb: KnowledgeBase | None, on: date) -> tuple[str, list[tuple[str, Decimal, str | None]]]:
    """Evaluate a template into concrete (account, amount, treatment) lines."""
    env: dict[str, Any] = {}
    for i in t.inputs:
        if i.name not in inputs:
            raise ExprError(f"missing input {i.name}")
        env[i.name] = Decimal(str(inputs[i.name])) if i.type in ("money", "number") else inputs[i.name]
    for name, ref in t.rules.items():
        if kb is None:
            raise ExprError("template needs the rule knowledge base")
        rid, _, key = ref.partition(":")
        value = kb.resolve(rid, on).value
        env[name] = Decimal(str(value[key] if key else value))
    lines = []
    for l in t.lines:
        if l.when and not evaluate(l.when, env):
            continue
        amount = Decimal(evaluate(l.amount, env)).quantize(Decimal("0.01"))
        if amount == 0:
            continue
        account = str(inputs[l.account[1:]]) if l.account.startswith("$") else l.account
        lines.append((account, amount, l.tax_treatment))
    memo = t.memo.format(**{k: v for k, v in inputs.items()})
    return memo, lines


def verify_pack(pack: DomainPack, packs: Packs | None, kb: KnowledgeBase | None) -> list[dict[str, Any]]:
    """Mechanical proof that an (AI-drafted) pack is internally consistent."""
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    base: dict[str, AccountDef] = {}
    if pack.extends and packs and pack.extends in packs.packs:
        base = packs.accounts(pack.extends)
    codes = [a.code for a in pack.accounts]
    add("accounts_unique", len(codes) == len(set(codes)), f"{len(codes)} accounts")
    clash = [c for c in codes if c in base and base[c].type != next(a.type for a in pack.accounts if a.code == c)]
    add("accounts_consistent_with_parent", not clash, ", ".join(clash))
    all_acc = {**base, **{a.code: a for a in pack.accounts}}
    for t in pack.templates:
        declared = {i.name for i in t.inputs} | set(t.rules)
        try:
            used = set().union(*[names_used(l.amount) | (names_used(l.when) if l.when else set()) for l in t.lines])
            add(f"template:{t.id}:names", used <= declared, f"undeclared: {sorted(used - declared)}" if used - declared else "ok")
            bad_acc = [l.account for l in t.lines if not l.account.startswith("$") and l.account not in all_acc]
            add(f"template:{t.id}:accounts", not bad_acc, f"unknown accounts {bad_acc}" if bad_acc else "ok")
            bad_t = [l.tax_treatment for l in t.lines if l.tax_treatment and l.tax_treatment not in TREATMENTS]
            add(f"template:{t.id}:treatments", not bad_t, f"unknown treatments {bad_t}" if bad_t else "ok")
            memo, lines = render(t, t.sample, kb, date.today())
            total = sum((a for _, a, _ in lines), Decimal(0))
            add(f"template:{t.id}:balances", total == 0 and len(lines) >= 2, f"sample total {total} over {len(lines)} lines")
        except Exception as e:
            add(f"template:{t.id}:evaluates", False, f"{type(e).__name__}: {e}")
    for c in pack.checks:
        refs = [v for k, v in c.params.items() if k in ("account", "numerator", "denominator")]
        refs += [x for k in ("assets", "liabilities", "accounts") for x in c.params.get(k, [])]
        missing = [r for r in refs if r not in all_acc]
        add(f"check:{c.id}:accounts", not missing, f"unknown accounts {missing}" if missing else "ok")
        if "pattern" in c.params:
            try:
                re.compile(c.params["pattern"])
                add(f"check:{c.id}:pattern", True)
            except re.error as e:
                add(f"check:{c.id}:pattern", False, str(e))
    return checks


def normalized_balances(balances: list[dict[str, Any]]) -> dict[str, Decimal]:
    out = {}
    for b in balances:
        v = Decimal(b["balance"])
        out[b["code"]] = -v if b["type"] in ("liability", "equity", "revenue") else v
    return out


def evaluate_kpis(kpis: list[KPI], balances: list[dict[str, Any]], facts: dict[str, Any]) -> list[dict[str, Any]]:
    bal = normalized_balances(balances)
    env: dict[str, Any] = {**{k: v for k, v in facts.items() if isinstance(v, (int, float, str, bool))}}
    out = []
    for k in kpis:
        names = names_used(k.expr)
        for n in names:
            if n.startswith("bal_"):
                env.setdefault(n, bal.get(n[4:], Decimal(0)))
        try:
            v = evaluate(k.expr, env)
            out.append({"id": k.id, "title": k.title, "value": str(Decimal(v).quantize(Decimal("0.01"))), "unit": k.unit})
        except (ExprError, ZeroDivisionError, ArithmeticError) as e:
            out.append({"id": k.id, "title": k.title, "value": None, "unit": k.unit, "missing": str(e)})
    return out
