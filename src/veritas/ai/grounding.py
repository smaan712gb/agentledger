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


def number_variants(value: float | int | Decimal) -> set[Decimal]:
    """Representations a source might use: as-is, as cents (0.725 -> 72.5), as percent (0.2 -> 20)."""
    d = Decimal(str(value))
    return {d.normalize(), (d * 100).normalize(), (d / 100).normalize()}


def number_supported(value: float | int | Decimal, evidence: Iterable[str]) -> bool:
    found: set[Decimal] = set()
    for e in evidence:
        found |= numbers_in(e)
    return bool(number_variants(value) & found)


TRIVIAL = {Decimal(n) for n in range(0, 11)}


def unsupported_numbers(answer: str, evidence_text: str, ignore_years: bool = True) -> list[str]:
    """Numbers in an answer that do not appear anywhere in the evidence it was given."""
    have = numbers_in(evidence_text)
    have_variants = set()
    for h in have:
        have_variants |= number_variants(h)
    bad = []
    for m in NUM.findall(answer):
        for d in numbers_in(m):
            if d in TRIVIAL or (ignore_years and d == d.to_integral() and 1900 <= d <= 2100):
                continue
            if d not in have_variants:
                bad.append(m)
            break
    return sorted(set(bad))


CITE = re.compile(r"\[(R|E|F|D|C|M|P):([^\]]+)\]")


def citations(answer: str) -> list[tuple[str, str]]:
    return [(k, v.strip()) for k, v in CITE.findall(answer)]


def _significant(text: str) -> list[str]:
    out = []
    for m in NUM.findall(text):
        for d in numbers_in(m):
            if d in TRIVIAL or (d == d.to_integral() and 1900 <= d <= 2100):
                continue
            out.append(m)
            break
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
        attributed: set[Decimal] = set()  # numbers already tied to a citation that contains them
        for para in [p for p in re.split(r"\n+", answer) if p.strip()]:
            para_cites = [f"{k}:{v}" for k, v in citations(para)]
            for sent in [x for x in re.split(r"(?<=[.!?])\s+(?=[A-Z*\[(\-])", para) if x.strip()]:
                nums = _significant(CITE.sub("", sent))
                if not nums:
                    continue
                here = [f"{k}:{v}" for k, v in citations(sent)] or para_cites
                cited: set[Decimal] = set()
                for h in numbers_in(" ".join(lines.get(c, "") for c in here)):
                    cited |= number_variants(h)
                for n in nums:
                    vals = numbers_in(n)
                    if vals & cited:
                        attributed |= vals
                    elif vals & attributed:
                        continue  # restating a number that was properly cited earlier
                    elif not here:
                        uncited.append(n)
                    else:
                        misattributed.append(n)
    ok = not unknown and not unsupported and not uncited and not misattributed
    return {"grounded": ok, "citations": [f"{k}:{v}" for k, v in cites], "unknown_citations": unknown,
            "unsupported_numbers": unsupported, "uncited_numbers": sorted(set(uncited)),
            "misattributed_numbers": sorted(set(misattributed))}
