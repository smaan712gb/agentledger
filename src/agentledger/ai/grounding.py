"""Deterministic grounding checks. The AI may write words; it may not invent facts.

Used two ways:
  * a regulatory proposal's numbers must appear in verbatim quotes, and the quotes
    must appear in the source document;
  * an Ask answer's numbers must appear in the evidence pack it was given, and its
    citations must point at evidence that exists.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

NUM = re.compile(r"(?<![\w.])\$?\(?-?\d[\d,]*(?:\.\d+)?\)?%?")
# A typed token: the number plus how the text qualifies it, so $5,000 can never vouch for $500,000.
TOKEN = re.compile(r"(?<![\w.])(\$)?\(?(-?\d[\d,]*(?:\.\d+)?)\)?\s*(%|percent\b|per cent\b|cents?\b)?", re.I)
WS = re.compile(r"\s+")


def norm_text(s: str) -> str:
    s = s.replace("’", "'").replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    s = s.replace(" ", " ")
    return WS.sub(" ", s).strip().lower()


def quote_in(quote: str, document: str) -> bool:
    q = norm_text(quote).strip(" .\"'")
    return bool(q) and q in norm_text(document)


def numbers_in(text: str) -> set[Decimal]:
    out: set[Decimal] = set()
    for m in NUM.findall(text):
        raw = m.replace("$", "").replace(",", "").replace("(", "").replace(")", "")
        pct = raw.endswith("%")
        raw = raw.rstrip("%")
        try:
            d = Decimal(raw)
        except InvalidOperation:
            continue
        out.add(d.normalize())
        if pct:
            out.add((d / 100).normalize())
    return out


def typed_numbers(text: str) -> list[tuple[Decimal, str]]:
    """(value, kind) for every number; kind is money ($), percent (% or 'percent'), cents, or plain."""
    out = []
    for dollar, raw, unit in TOKEN.findall(text):
        try:
            d = Decimal(raw.replace(",", "")).normalize()
        except InvalidOperation:
            continue
        u = (unit or "").lower()
        kind = "percent" if u.startswith(("%", "percent", "per cent")) else "cents" if u.startswith("cent") else \
            "money" if dollar else "plain"
        out.append((d, kind))
    return out


def same_quantity(a: tuple[Decimal, str], b: tuple[Decimal, str]) -> bool:
    """Equal values with compatible units. Percent and fraction convert (20% = 0.20); cents convert to dollars
    (72.5 cents = $0.725). Nothing else is ever scaled: a money amount must match exactly."""
    (va, ka), (vb, kb) = a, b
    if ka == kb:
        return va == vb
    pair = {ka, kb}
    if pair == {"money", "plain"}:
        return va == vb
    if pair == {"percent", "plain"}:
        pct, other = (va, vb) if ka == "percent" else (vb, va)
        return (pct / 100).normalize() == other or pct == other
    if pair in ({"cents", "money"}, {"cents", "plain"}):
        cents, other = (va, vb) if ka == "cents" else (vb, va)
        return (cents / 100).normalize() == other
    return False


def number_supported(value: float | int | Decimal, evidence: Iterable[str], unit: str | None = None) -> bool:
    """Is a rule value stated in the evidence? A rate (value <= 1) may be written as a percent, and a dollar
    amount as cents; otherwise the number must appear as is."""
    v = Decimal(str(value)).normalize()
    kinds = ["plain", "money"]
    if unit in ("fraction", "rate", "percent") or (unit is None and abs(v) <= 1):
        kinds.append("fraction")
    for e in evidence:
        for tok in typed_numbers(e):
            if tok[0] == v and tok[1] in ("plain", "money"):
                return True
            if "fraction" in kinds and tok[1] == "percent" and (tok[0] / 100).normalize() == v:
                return True
            if tok[1] == "cents" and (tok[0] / 100).normalize() == v:
                return True
    return False


def _supported_by(tok: tuple[Decimal, str], have: list[tuple[Decimal, str]]) -> bool:
    return any(same_quantity(tok, h) for h in have)


TRIVIAL = {Decimal(n) for n in range(0, 11)}


def _trivial(tok: tuple[Decimal, str], ignore_years: bool = True) -> bool:
    """Only a bare number can be a year or a small count. "$2,000", "20%" or "50 cents" is always a claim to check."""
    d, kind = tok
    if kind != "plain":
        return False
    return d in TRIVIAL or (ignore_years and d == d.to_integral() and 1900 <= d <= 2100)


def unsupported_numbers(answer: str, evidence_text: str, ignore_years: bool = True) -> list[str]:
    """Numbers in an answer that the evidence does not state, compared as typed quantities."""
    have = typed_numbers(evidence_text)
    bad = []
    for m in TOKEN.finditer(answer):
        tok = typed_numbers(m.group(0))
        if not tok or _trivial(tok[0], ignore_years):
            continue
        if not _supported_by(tok[0], have):
            bad.append(m.group(0).strip())
    return sorted(set(bad))


CITE = re.compile(r"\[(R|E|F|D|C|M|P):([^\]]+)\]")


def citations(answer: str) -> list[tuple[str, str]]:
    return [(k, v.strip()) for k, v in CITE.findall(answer)]


def _significant(text: str) -> list[str]:
    out = []
    for m in TOKEN.finditer(text):
        tok = typed_numbers(m.group(0))
        if tok and not _trivial(tok[0]):
            out.append(m.group(0).strip())
    return out


def check_answer(answer: str, evidence_text: str, known_ids: dict[str, set[str]],
                 lines: dict[str, str] | None = None) -> dict[str, Any]:
    """Verify an answer against its evidence pack.

    Always: every citation must exist and every number must appear in the evidence.
    With `lines` (citation -> evidence text): attribution too — each paragraph that states a
    number must cite evidence, and the cited items must contain that number.
    """
    cites = citations(answer)
    unknown = [f"{k}:{v}" for k, v in cites if v not in known_ids.get(k, set())]
    unsupported = unsupported_numbers(CITE.sub("", answer), evidence_text)
    uncited: list[str] = []
    misattributed: list[str] = []
    if lines is not None:
        attributed_tokens: list[tuple[Decimal, str]] = []  # quantities already tied to a citation that states them
        for para in [p for p in re.split(r"\n+", answer) if p.strip()]:
            para_cites = [f"{k}:{v}" for k, v in citations(para)]
            for sent in [x for x in re.split(r"(?<=[.!?])\s+(?=[A-Z*\[(\-])", para) if x.strip()]:
                nums = _significant(CITE.sub("", sent))
                if not nums:
                    continue
                here = [f"{k}:{v}" for k, v in citations(sent)] or para_cites
                cited = typed_numbers(" ".join(lines.get(c, "") for c in here))
                for n in nums:
                    tok = typed_numbers(n)
                    if not tok:
                        continue
                    if _supported_by(tok[0], cited):
                        attributed_tokens.append(tok[0])
                    elif _supported_by(tok[0], attributed_tokens):
                        continue  # restating a number that was properly cited earlier
                    elif not here:
                        uncited.append(n)
                    else:
                        misattributed.append(n)
    ok = not unknown and not unsupported and not uncited and not misattributed
    return {"grounded": ok, "citations": [f"{k}:{v}" for k, v in cites], "unknown_citations": unknown,
            "unsupported_numbers": unsupported, "uncited_numbers": sorted(set(uncited)),
            "misattributed_numbers": sorted(set(misattributed))}
