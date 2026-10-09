"""ATS scenario fixtures (backlog T2-04): the IRS Assurance Testing System scenarios as independent fixtures for the 1040 engine.

The IRS posts the TY2026 Form 1040 MeF ATS scenarios (Publication 1436) as PDFs: a cover page with the taxpayer's facts and the
forms, partly or wholly filled in. This script reads every scenario PDF found in docs/irs/ats-ty2026 (the owner's download,
kept untracked) and writes one JSON fixture per scenario to tests/fixtures/ats/scenario-NN.json:

- `return`: the facts the scenario states, in the engine's IndividualReturn shape where the model has a field (names and SSNs
  as printed: they are IRS test identities, not real people);
- `unmodelled`: every fact the scenario states that the model cannot hold, with its effect;
- `assumptions`: values the model requires that the scenario does not state (a filing status left unchecked, a day of birth
  when only the year is printed), each with the evidence and, where it matters, alternative values the harness re-runs;
- `expected`: every amount the scenario's forms print on a numbered line, keyed by the engine's form keys and line numbers
  (and the PDF's own form and line), with the page it came from; lines whose value the fixture feeds to the engine as an
  input are marked `role: input` and are not scored;
- `ambiguities`: whatever the text does not settle (an unreadable box, a value the form's own arithmetic contradicts);
- `source`: the PDF's file name, SHA-256, page count, the pages used, the extraction date and the pypdf version;
- `recorded`: the engine's outcome when the fixture was last recorded (matched lines, known gaps), written only by
  `--record` and otherwise carried over unchanged, so that tests/test_ats_scenarios.py fails when a gap closes silently.

Nothing here computes a tax amount: every number in a fixture's facts and expected lines is printed in the PDF (or is the sum
of printed entries where the model takes one total, as its provenance says), or is an assumption listed as such. Only
`recorded` holds the engine's amounts.

Usage (from the repository root; the PDFs are not committed, so extraction runs where the owner keeps them):
    python -I scripts/ats_fixtures.py               extract every scenario PDF found and write the fixtures
    python -I scripts/ats_fixtures.py --record      also run the engine and record each outcome (a scenario whose PDF is
                                                    absent is re-recorded from its committed fixture)
    python -I scripts/ats_fixtures.py --check       extract and compare with the committed fixtures without writing
    python -I scripts/ats_fixtures.py --dump 14     print what the reader sees in scenario 14, page by page
    python -I scripts/ats_fixtures.py --table       print the per-scenario summary table for docs/ATS.md
    --only N (repeatable) limits extraction and recording to scenario N.

The PDFs' text is read with pypdf (content streams parsed by pypdf; positions and font decoding done here, because several
scenario PDFs embed Arial subsets whose ToUnicode maps glyph ids to themselves: the text then reads as control characters,
and the glyph id of a TrueType Arial subset is the character's code minus 29, which this script undoes).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]
PDF_DIR = REPO / "docs" / "irs" / "ats-ty2026"
OUT_DIR = REPO / "tests" / "fixtures" / "ats"
TAX_YEAR = 2026

# ------------------------------------------------------------------------------------------------------------------ fonts

GLYPHS = {
    "space": " ", "exclam": "!", "quotedbl": '"', "numbersign": "#", "dollar": "$", "percent": "%", "ampersand": "&",
    "quotesingle": "'", "quoteright": "’", "parenleft": "(", "parenright": ")", "asterisk": "*", "plus": "+",
    "comma": ",", "hyphen": "-", "period": ".", "slash": "/", "zero": "0", "one": "1", "two": "2", "three": "3",
    "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "colon": ":", "semicolon": ";",
    "less": "<", "equal": "=", "greater": ">", "question": "?", "at": "@", "bracketleft": "[", "backslash": "\\",
    "bracketright": "]", "asciicircum": "^", "underscore": "_", "grave": "`", "quoteleft": "‘", "braceleft": "{",
    "bar": "|", "braceright": "}", "asciitilde": "~", "bullet": "•", "endash": "–", "emdash": "—",
    "quotedblleft": "“", "quotedblright": "”", "section": "§", "copyright": "©", "registered": "®",
    "trademark": "™", "degree": "°", "dagger": "†", "ellipsis": "…", "minus": "−", "fi": "ﬁ",
    "fl": "ﬂ", "nbspace": " ", "periodcentered": "·", "multiply": "×", "paragraph": "¶",
    "cent": "¢", "sterling": "£", "yen": "¥", "Euro": "€", "quotesinglbase": "‚",
    "quotedblbase": "„", "guillemotleft": "«", "guillemotright": "»",
}
GLYPHS.update({c: c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"})
CHECK = "✔"            # the check mark the scenarios print (AdobePiStd uni2714)
UNMAPPED = "�"
# Fonts that print the IRS forms' own text. Everything else on a form page was typed into it by the scenario's author.
TEMPLATE_FONT = re.compile(r"HelveticaNeue|FranklinGothic|TimesLTStd|UniversLTStd|OCRAStd|HelveticaLTStd-Roman|TektonPro|"
                           r"Symbol|ZapfDingbats|Wingdings|AdobePiStd-Identity|Times-Roman|CIDFont")


def _glyph(name: str) -> str:
    if name in GLYPHS:
        return GLYPHS[name]
    m = re.fullmatch(r"uni([0-9A-Fa-f]{4})", name) or re.fullmatch(r"u([0-9A-Fa-f]{4,6})", name)
    if m:
        return chr(int(m.group(1), 16))
    return GLYPHS.get(name.split(".")[0], UNMAPPED)


def _mul(a: tuple, b: tuple) -> tuple:
    return (a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3], a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5])


def _parse_cmap(data: str) -> dict[int, str]:
    """bfchar and bfrange entries of a ToUnicode CMap."""
    out: dict[int, str] = {}

    def u(hexs: str) -> str:
        try:
            return bytes.fromhex(hexs).decode("utf-16-be")
        except (UnicodeDecodeError, ValueError):
            return UNMAPPED
    for block in re.findall(r"beginbfchar(.*?)endbfchar", data, re.S):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
            out[int(src, 16)] = u(dst)
    for block in re.findall(r"beginbfrange(.*?)endbfrange", data, re.S):
        for lo, hi, rest in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]*>)", block):
            lo_i, hi_i = int(lo, 16), int(hi, 16)
            if hi_i - lo_i > 0xFFFF:
                continue
            if rest.startswith("["):
                for k, d in enumerate(re.findall(r"<([0-9A-Fa-f]*)>", rest)):
                    out[lo_i + k] = u(d)
            elif len(rest) > 2:
                base = bytes.fromhex(rest[1:-1])
                start = int.from_bytes(base, "big")
                for k in range(hi_i - lo_i + 1):
                    out[lo_i + k] = u((start + k).to_bytes(len(base), "big").hex())
    return out


@dataclass
class Font:
    name: str
    two_byte: bool
    cmap: dict[int, str]
    widths: dict[int, float]
    default_width: float
    # A Type0 font whose ToUnicode maps glyph ids to themselves (code 0x14 to U+0014): the TrueType Arial subsets of the
    # scenario PDFs keep Arial's glyph order, where the glyph id of a printable ASCII character is its code minus 29.
    identity_glyphs: bool

    def decode(self, raw: bytes) -> list[tuple[int, str]]:
        codes = [int.from_bytes(raw[i:i + 2], "big") for i in range(0, len(raw) - 1, 2)] if self.two_byte else list(raw)
        if self.identity_glyphs:
            return [(c, chr(c + 29) if 3 <= c <= 97 else UNMAPPED) for c in codes]
        return [(c, self.cmap.get(c, UNMAPPED)) for c in codes]

    def width(self, code: int) -> float:
        return self.widths.get(code, self.default_width)


def _resolve(obj: Any) -> Any:
    return obj.get_object() if hasattr(obj, "get_object") else obj


def load_font(ref: Any) -> Font:
    fd = _resolve(ref)
    name = str(fd.get("/BaseFont", "?"))
    two = str(fd.get("/Subtype")) == "/Type0"
    tounicode: dict[int, str] = {}
    if fd.get("/ToUnicode") is not None:
        try:
            tounicode = _parse_cmap(_resolve(fd["/ToUnicode"]).get_data().decode("latin-1"))
        except Exception:  # a damaged CMap reads as no CMap
            tounicode = {}
    identity = two and sum(1 for c in range(3, 98) if tounicode.get(c) == chr(c)) >= 20
    widths: dict[int, float] = {}
    if two:
        desc = _resolve(fd["/DescendantFonts"][0])
        default = float(desc.get("/DW", 1000))
        w = [_resolve(x) for x in _resolve(desc.get("/W", []))]
        i = 0
        while i < len(w):
            first, nxt = int(w[i]), w[i + 1]
            if isinstance(nxt, (list, tuple)) or (hasattr(nxt, "__len__") and not isinstance(nxt, (str, bytes))):
                for k, width in enumerate(nxt):
                    widths[first + k] = float(_resolve(width))
                i += 2
            else:
                for c in range(first, int(nxt) + 1):
                    widths[c] = float(w[i + 2])
                i += 3
        return Font(name, True, tounicode, widths, default, identity)
    first_char = int(fd.get("/FirstChar", 0))
    listed = _resolve(fd.get("/Widths"))
    for k, width in enumerate(listed or []):
        widths[first_char + k] = float(_resolve(width))
    descriptor = _resolve(fd.get("/FontDescriptor"))
    default = float(descriptor.get("/MissingWidth", 0) or 0) if descriptor is not None else 0.0
    if not listed:
        default = 500.0  # a standard 14 font without widths: positions inside a run are approximate (only label text uses them)
    cmap: dict[int, str] = {}
    enc = _resolve(fd.get("/Encoding"))
    base, diffs = "WinAnsiEncoding", {}
    if enc is not None and hasattr(enc, "get"):
        base = str(enc.get("/BaseEncoding", "/WinAnsiEncoding")).lstrip("/")
        code = 0
        for item in _resolve(enc.get("/Differences", [])):
            item = _resolve(item)
            if isinstance(item, (int, float)):
                code = int(item)
            else:
                diffs[code] = _glyph(str(item).lstrip("/"))
                code += 1
    elif enc is not None:
        base = str(enc).lstrip("/")
    codec = "mac_roman" if base == "MacRomanEncoding" else "cp1252"
    for c in range(256):
        if c in diffs:
            cmap[c] = diffs[c]
        else:
            try:
                cmap[c] = bytes([c]).decode(codec)
            except UnicodeDecodeError:
                cmap[c] = UNMAPPED
    for c, u in tounicode.items():  # a ToUnicode entry wins over the encoding, unless it names a control character
        if not (len(u) == 1 and ord(u) < 32 and cmap.get(c, UNMAPPED) not in (UNMAPPED,) and ord(cmap[c][0]) >= 32):
            cmap[c] = u
    return Font(name, False, cmap, widths, default, False)


# ------------------------------------------------------------------------------------------------------------------ text positions

@dataclass
class Word:
    """One whitespace-separated token of a shown string, in PDF points from the page's lower-left corner."""
    x0: float
    x1: float
    y: float
    text: str
    font: str
    fill: bool          # typed into the form by the scenario's author (not the IRS form's printed text)
    run: int            # index of the shown string it came from
    rotated: bool = False  # printed vertically (a landscape table page)

    def __repr__(self) -> str:
        return f"<{self.text!r} {self.x0:.0f}-{self.x1:.0f}@{self.y:.0f}{' fill' if self.fill else ''}>"


@dataclass
class Page:
    number: int
    words: list[Word]
    runs: list[tuple[int, str, str, bool]]        # (run index, text, font, fill) in content order
    unmapped: int                                 # glyphs no CMap or encoding could name
    repaired: int                                 # glyphs decoded through the identity-glyph repair

    def template_text(self) -> str:
        return " ".join(t for _, t, f, fill in self.runs if not fill)

    def fill_text(self) -> str:
        return " ".join(t for _, t, f, fill in self.runs if fill)


def read_page(reader: Any, page_obj: Any, number: int) -> Page:
    from pypdf.generic import ContentStream

    words: list[Word] = []
    runs: list[tuple[int, str, str, bool]] = []
    counters = {"unmapped": 0, "repaired": 0}

    def walk(content: Any, resources: Any, ctm: tuple) -> None:
        fonts = _resolve(resources.get("/Font")) if resources is not None and resources.get("/Font") is not None else {}
        xobjects = _resolve(resources.get("/XObject")) if resources is not None and resources.get("/XObject") is not None else {}
        cache: dict[str, Font] = {}
        stack: list[tuple[Any, ...]] = []
        tm = tlm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        tc = tw = ts = tl = 0.0
        th = 1.0
        font: Font | None = None
        size = 0.0

        def show(items: list[Any]) -> None:
            nonlocal tm
            if font is None:
                return
            glyphs: list[tuple[str, float, float, float]] = []   # (char, x0, x1, y)
            direction = _mul(tm, ctm)
            rotated = abs(direction[1]) > abs(direction[0])
            for item in items:
                if isinstance(item, (int, float)) or type(item).__name__ in ("FloatObject", "NumberObject"):
                    tm = _mul((1, 0, 0, 1, -float(item) / 1000.0 * size * th, 0), tm)
                    if abs(float(item)) > 1500:
                        glyphs.append((" ", 0, 0, 0))
                    continue
                raw = item.original_bytes if hasattr(item, "original_bytes") else bytes(str(item), "latin-1")
                for code, ch in font.decode(raw):
                    m = _mul((1, 0, 0, 1, 0, ts), _mul(tm, ctm))
                    x0, y0 = m[4], m[5]
                    advance = (font.width(code) / 1000.0 * size + tc + (tw if (not font.two_byte and code == 32) else 0.0)) * th
                    tm = _mul((1, 0, 0, 1, advance, 0), tm)
                    x1 = _mul(tm, ctm)[4]
                    if ch == UNMAPPED:
                        counters["unmapped"] += 1
                    elif font.identity_glyphs:
                        counters["repaired"] += 1
                    glyphs.append((ch, x0, x1, y0))
            text = "".join(g[0] for g in glyphs)
            if not text.strip():
                return
            idx = len(runs)
            fill = not TEMPLATE_FONT.search(font.name) or CHECK in text
            runs.append((idx, text.strip(), font.name, fill))
            current: list[tuple[str, float, float, float]] = []

            def emit() -> None:
                if current:
                    words.append(Word(round(current[0][1], 1), round(current[-1][2], 1), round(current[0][3], 1),
                                      "".join(c[0] for c in current), font.name if font else "?", fill, idx, rotated))
            for g in glyphs + [(" ", 0, 0, 0)]:
                if g[0].isspace():
                    emit()
                    current = []
                elif g[0] == CHECK:     # every check mark is a word of its own (one string may tick two boxes)
                    emit()
                    current = [g]
                    emit()
                    current = []
                else:
                    current.append(g)

        for operands, op in ContentStream(content, reader).operations:
            if op == b"q":   # the text state (font, size, spacing) is part of the graphics state that q saves and Q restores
                stack.append((ctm, font, size, tc, tw, th, tl, ts))
            elif op == b"Q":
                if stack:
                    ctm, font, size, tc, tw, th, tl, ts = stack.pop()
            elif op == b"cm":
                ctm = _mul(tuple(float(v) for v in operands), ctm)
            elif op == b"BT":
                tm = tlm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
            elif op == b"Tf":
                key = str(operands[0])
                if key not in cache and key in fonts:
                    cache[key] = load_font(fonts[key])
                font, size = cache.get(key), float(operands[1])
            elif op == b"Tc":
                tc = float(operands[0])
            elif op == b"Tw":
                tw = float(operands[0])
            elif op == b"Tz":
                th = float(operands[0]) / 100.0
            elif op == b"TL":
                tl = float(operands[0])
            elif op == b"Ts":
                ts = float(operands[0])
            elif op in (b"Td", b"TD"):
                if op == b"TD":
                    tl = -float(operands[1])
                tlm = tm = _mul((1, 0, 0, 1, float(operands[0]), float(operands[1])), tlm)
            elif op == b"Tm":
                tlm = tm = tuple(float(v) for v in operands)
            elif op == b"T*":
                tlm = tm = _mul((1, 0, 0, 1, 0, -tl), tlm)
            elif op == b"Tj":
                show([operands[0]])
            elif op == b"TJ":
                show(list(operands[0]))
            elif op == b"'":
                tlm = tm = _mul((1, 0, 0, 1, 0, -tl), tlm)
                show([operands[0]])
            elif op == b'"':
                tw, tc = float(operands[0]), float(operands[1])
                tlm = tm = _mul((1, 0, 0, 1, 0, -tl), tlm)
                show([operands[2]])
            elif op == b"Do" and str(operands[0]) in xobjects:
                xo = _resolve(xobjects[str(operands[0])])
                if str(xo.get("/Subtype")) == "/Form":
                    matrix = tuple(float(v) for v in xo.get("/Matrix", [1, 0, 0, 1, 0, 0]))
                    res = _resolve(xo.get("/Resources")) if xo.get("/Resources") is not None else resources
                    walk(xo, res, _mul(matrix, ctm))

    resources = _resolve(page_obj.get("/Resources"))
    walk(page_obj.get_contents(), resources, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0))
    return Page(number, words, runs, counters["unmapped"], counters["repaired"])


# ------------------------------------------------------------------------------------------------------------------ forms on pages

# Page titles, most specific first: (pattern on the page's printed text, form, page of the form). Form ids are the IRS form;
# returns/ats.py maps them to engine form keys.
PAGE_TITLES: list[tuple[str, str, int]] = [
    (r"ATS Test\s+Scenario", "cover", 1),
    (r"Application for Automatic Extension of Time", "f4868", 1),   # before Form 1040: its title names that return
    (r"Schedule 8812 \(Form 1040\) 2026 Page 2", "sch_8812", 2), (r"SCHEDULE 8812", "sch_8812", 1),
    (r"Schedule 1-A \(Form 1040\) \(2026\) Page 3", "sch_1a", 3), (r"Schedule 1-A \(Form 1040\) \(2026\) Page 2", "sch_1a", 2),
    (r"SCHEDULE 1-A", "sch_1a", 1),
    (r"Schedule 1 \(Form 1040\) 2026 Page 2", "sch_1", 2), (r"SCHEDULE 1\b", "sch_1", 1),
    (r"Schedule 2 \(Form 1040\) 2026 Page 2", "sch_2", 2), (r"SCHEDULE 2\b", "sch_2", 1),
    (r"SCHEDULE 3-A", "sch_3a", 1),
    (r"Schedule 3 \(Form 1040\) 2026 Page 2", "sch_3", 2), (r"SCHEDULE 3\b", "sch_3", 1),
    (r"SCHEDULE EIC", "sch_eic", 1),
    (r"Schedule A \(Form 4136\) \(12-2025\) Page (\d)", "f4136_sch_a", 0), (r"Business Activity Report for Credit", "f4136_sch_a", 1),
    (r"Form 4136 \(2026\) Page (\d)", "f4136", 0), (r"Credit for Federal Tax Paid on Fuels", "f4136", 1),
    (r"SCHEDULE A.{0,120}\(Form 1062\)", "f1062_sch_a", 1), (r"SCHEDULE A.{0,80}\(Form 3800\)", "f3800_sch_a", 1),
    (r"Schedule A \(Form 1040\) 2026 Page 2", "sch_a", 2), (r"SCHEDULE A\b.{0,60}Itemized Deductions", "sch_a", 1),
    (r"Schedule C \(Form 1040\) 2026 Page 2", "sch_c", 2), (r"SCHEDULE C\b", "sch_c", 1),
    (r"Schedule D \(Form 1040\) 2026 Page 2", "sch_d", 2), (r"SCHEDULE D\b", "sch_d", 1),
    (r"Schedule E \(Form 1040\) 2026 Attachment Sequence No\. 13 Page 3", "sch_e", 3),
    (r"Schedule E \(Form 1040\) 2026 Attachment Sequence No\. 13 Page 2", "sch_e", 2),
    (r"SCHEDULE E\b|Supplemental Income and Loss", "sch_e", 1),
    (r"Schedule F \(Form 1040\) 2026 Page 2", "sch_f", 2), (r"SCHEDULE F\b", "sch_f", 1),
    (r"Schedule H \(Form 1040\) 2026 Page 2", "sch_h", 2), (r"SCHEDULE H\b", "sch_h", 1),
    (r"Schedule SE \(Form 1040\) 2026 Page 2", "sch_se", 2), (r"SCHEDULE SE\b", "sch_se", 1),
    (r"Form 1040 \(2026\)\s*Page\s*2", "f1040", 2), (r"U\.S\. Individual Income Tax Return", "f1040", 1),
    (r"Form 2441 \(2026\) Page 2", "f2441", 2), (r"Child and Dependent Care Expenses", "f2441", 1),
    (r"Form 8862 \(Rev\. 12-2025\)\s+Page 3", "f8862", 3), (r"Form 8862 \(Rev\. 12-2025\)\s+Page 2", "f8862", 2),
    (r"Information To Claim Certain Credits After Disallowance", "f8862", 1),
    (r"Page 2 Form 8863 \(2026\)|Form 8863 \(2026\) Page 2", "f8863", 2), (r"Education Credits", "f8863", 1),
    (r"Form 8283 \(Rev\. 12-2025\) Page 2", "f8283", 2), (r"Noncash Charitable Contributions", "f8283", 1),
    (r"Allocation of Refund", "f8888", 1), (r"Carryforward of Residential Energy Credit|Residential Energy Credit", "f5695", 1),
    (r"Farm Rental Income and Expenses", "f4835", 1),
    (r"Form 3800 \(2026\) Page 2", "f3800", 2), (r"General Business Credit", "f3800", 1),
    (r"Deferral of Payment of Tax on Gain From the Sale or Exchange", "f1062", 1),
    (r"4562-B", "f4562b", 1), (r"Energy Efficient Commercial Buildings Deduction", "f7205", 1),
    (r"Form 7207 \(Rev\. 12-2025\) Page", "f7207", 2), (r"Advanced Manufacturing Production Credit", "f7207", 1),
    (r"Prevailing Wage and Apprenticeship", "f7220", 1),
    (r"Certain Gambling Winnings|Reportable winnings", "w2g", 1),
    (r"Distributions From\s+Pensions|Gross distribution", "f1099r", 1),
    (r"Wage and Tax Statement|Employee.s social security number", "w2", 1),
]


def rotated_page(page: Page) -> bool:
    printed = [w for w in page.words if not w.fill]
    return bool(printed) and sum(w.rotated for w in printed) > len(printed) / 2


def classify(page: Page) -> tuple[str, int]:
    text = re.sub(r"\s+", " ", page.template_text())
    cover = re.sub(r"\s+", " ", page.fill_text())
    if re.search(r"ATS Test\s+Scenario", cover):
        return "cover", 1
    if rotated_page(page):
        # Landscape table pages (Form 3800 Part III, Form 7220 Parts II-V) print the form number among the column headers.
        for number, form in (("3800", "f3800"), ("7220", "f7220"), ("7207", "f7207"), ("4136", "f4136")):
            if re.search(rf"\b{number}\b", text):
                return form, 3
        return "unknown", 0
    for pattern, form, part in PAGE_TITLES:
        m = re.search(pattern, text)
        if m:
            return form, (int(m.group(1)) if part == 0 else part)
    return "unknown", 0


# ------------------------------------------------------------------------------------------------------------------ reading lines

LINE_LABEL = re.compile(r"^(\d{1,2}[a-z]?|[a-z])$")
AMOUNT = re.compile(r"^\(?-?\$?(\d{1,3}(,\d{3})+|\d+)(\.\d{1,2})?\)?\.?$")
ROW_TOLERANCE = 4.5

# Forms whose typed amounts sit on numbered lines and are read as line values. Pages of other forms (Schedule EIC, Forms 8283,
# 8862, 8888 and the tables of Forms 3800, 4136, 7207 and 7220) are tables or boxes: a scenario reads what it needs from them
# explicitly, and their other entries are listed in the fixture's `pages` as not read.
LINE_FORMS = {
    "f1040": None, "sch_1": None, "sch_1a": None, "sch_2": None, "sch_3": None, "sch_3a": None, "sch_8812": None,
    "sch_a": None, "sch_b": None, "sch_c": None, "sch_d": None, "sch_e": None, "sch_f": None, "sch_h": None, "sch_se": None,
    "f2441": None, "f8863": {1}, "f4835": None, "f5695": None, "f1062": None, "f1062_sch_a": None, "f3800": {1, 2},
    "f4136": {4}, "f4868": None,
}


# Numbered lines that hold facts rather than amounts (a vehicle's dates and miles), read by the scenario that needs them.
LINE_SKIP: dict[tuple[str, int], str] = {
    ("sch_c", 2): r"4[3-7][a-c]?",                       # Part IV, the vehicle (Part V's rows carry no line number)
    ("f2441", 1): r"1[a-e]|2",                           # the care providers and qualifying persons tables
}
REFERENCE_WORDS = {"line", "lines", "form", "forms", "schedule", "column", "columns", "part", "box", "boxes", "and", "or",
                   "to", "from", "of", "on", "see"}


def _edge_words(page: Page) -> set[int]:
    """Indexes (into page.words) of the words that can be printed line labels: the first and last word of each shown string
    (a label is a string of its own, the first word of a line's text, or the box label after its dot leaders), never a
    number inside a sentence, and never a reference at the end of one ("... go to line 1b")."""
    first: dict[int, int] = {}
    last: dict[int, int] = {}
    previous: dict[int, str] = {}
    for i, w in enumerate(page.words):
        if w.text.strip(".") == "":
            continue
        first.setdefault(w.run, i)
        last[w.run] = i
    for i, w in enumerate(page.words):
        if i > 0 and page.words[i - 1].run == w.run:
            previous[i] = page.words[i - 1].text.lower().strip(",.;:()")
    return {i for i in set(first.values()) | set(last.values()) if previous.get(i, "") not in REFERENCE_WORDS}


def _noise(page: Page, w: Word) -> bool:
    """Typed digits that are not amounts: one character per box (an SSN, an IP PIN, a business code), identification numbers
    and ZIP codes, and the name and SSN block at the top of each page."""
    if re.fullmatch(r"\d{3}-?\d{2}-?\d{4}|\d{2}-\d{7}|\d{5}(-\d{4})?|\d{9}", w.text):
        return True
    if max((x.y for x in page.words), default=792.0) - w.y < 110:
        return True
    singles = [x for x in page.words if x.fill and len(x.text) <= 2 and abs(x.y - w.y) <= ROW_TOLERANCE]
    return len(singles) >= 3


def fill_lines(page: Page, *, tolerance: float = 2.5) -> list[tuple[float, float, str]]:
    """Typed text grouped by baseline: (y, x of the first word, text), words closer than 1.2 points joined without a space
    (one value split across fonts or strings), others with one space; top to bottom, left to right."""
    words = sorted((w for w in page.words if w.fill), key=lambda w: (-w.y, w.x0))
    rows: list[list[Word]] = []
    for w in words:
        for row in rows:
            if abs(row[0].y - w.y) <= tolerance:
                row.append(w)
                break
        else:
            rows.append([w])
    out = []
    for row in rows:
        row.sort(key=lambda w: w.x0)
        text = row[0].text
        for prev, w in zip(row, row[1:]):
            text += ("" if w.x0 - prev.x1 < 1.2 else " ") + w.text
        out.append((row[0].y, row[0].x0, text))
    return sorted(out, key=lambda t: (-t[0], t[1]))


def parse_amount(text: str) -> int | None:
    t = text.strip()
    if not AMOUNT.match(t):
        return None
    negative = t.startswith("(") or t.startswith("-")
    digits = re.sub(r"[^\d.]", "", t).rstrip(".")
    if digits.count(".") > 1:
        return None
    value = float(digits) if digits else 0.0
    if value != int(value):
        return None  # cents are never printed on these forms' line amounts; a decimal is read as not an amount
    return -int(value) if negative else int(value)


@dataclass
class LineValue:
    form: str
    part: int
    page: int
    line: str
    value: int
    raw: str
    x0: float
    x1: float
    y: float
    column: str | None = None
    inline: bool = False


def column_headers(page: Page) -> list[Word]:
    """Printed column letters that head a column ("(d) Proceeds"), not references to one ("Subtract column (e) from ...")."""
    out = []
    for i, w in enumerate(page.words):
        if w.fill or not re.fullmatch(r"\([a-k]\)", w.text):
            continue
        before = page.words[i - 1] if i > 0 and page.words[i - 1].run == w.run else None
        if before is not None and before.text.lower().strip(",.;:") in REFERENCE_WORDS | {"in"}:
            continue
        out.append(w)
    return out


def read_lines(page: Page, form: str, part: int) -> tuple[list[LineValue], list[dict[str, Any]]]:
    """Amounts typed on numbered lines: each amount belongs to the rightmost printed line label to its left on the same
    baseline (the box label at the right margin, or the label of a sub-column such as 25a or 4a)."""
    edges = _edge_words(page)
    labels = [w for i, w in enumerate(page.words)
              if not w.fill and i in edges and LINE_LABEL.match(w.text) and not re.fullmatch(r"[a-z]", w.text)]
    margin = [lab for lab in labels if lab.x0 < 75]
    amounts = [(w, parse_amount(w.text)) for w in page.words if w.fill and parse_amount(w.text) is not None and w.x0 >= 200]
    rows: dict[str, list[tuple[Word, int]]] = {}
    issues: list[dict[str, Any]] = []
    for w, value in amounts:
        assert value is not None
        same_row = [lab for lab in labels if abs(lab.y - w.y) <= ROW_TOLERANCE and lab.x1 <= w.x0 + 2]
        if not same_row and w.x0 > 280:
            # A line whose text runs over several printed lines takes its amounts on the last of them, with its number at
            # the left margin of the first: the nearest margin label above, when no other margin label lies in between.
            above = [lab for lab in margin if 0 < lab.y - w.y <= 80]
            below = [lab for lab in margin if -ROW_TOLERANCE < lab.y - w.y <= 0]
            if above and not below:
                same_row = [min(above, key=lambda lab: lab.y - w.y)]
        if not same_row:
            if w.x0 > 380 and not _noise(page, w):   # an amount on no numbered line: say so, never drop it silently
                issues.append({"page": page.number, "form": form, "kind": "amount_without_line", "text": w.text,
                               "x": w.x0, "y": w.y})
            continue
        label = max(same_row, key=lambda lab: lab.x0)
        skip = LINE_SKIP.get((form, part))
        if skip and re.fullmatch(skip, label.text):
            continue
        if label.text == "20" and w.text == "26":
            continue  # the tax year typed after the form's printed "20"
        rows.setdefault(label.text, []).append((w, value))
    out: list[LineValue] = []
    headers = column_headers(page)
    for line, items in rows.items():
        items.sort(key=lambda it: it[0].x0)
        if len(items) == 1:
            w, value = items[0]
            out.append(LineValue(form, part, page.number, line, value, w.text, w.x0, w.x1, w.y))
            continue
        # Several amounts on one line: columns by the printed column headers when the form has them, else the rightmost
        # amount is the line's and the others are entries inside the line (a count, a rate base), kept as inline values.
        placed = False
        if headers:
            cols: list[str | None] = []
            for w, value in items:
                above = [h for h in headers if h.y > w.y]
                if not above:
                    cols.append(None)
                    continue
                nearest = min(h.y - w.y for h in above)         # the header row nearest above the line, not another table's
                row = [h for h in above if h.y - w.y - nearest <= 12 and h.x0 <= w.x1 + 2]
                cols.append(max(row, key=lambda h: h.x0).text.strip("()") if row else None)
            if all(cols) and len(set(cols)) == len(cols):
                for (w, value), col in zip(items, cols):
                    out.append(LineValue(form, part, page.number, line, value, w.text, w.x0, w.x1, w.y, column=col))
                placed = True
        if not placed:
            *inner, (w, value) = items
            out.append(LineValue(form, part, page.number, line, value, w.text, w.x0, w.x1, w.y))
            # Single characters typed one per box (a VIN, an EIN) are not amounts: a row with three or more one-character
            # entries is such a string, read by the scenario as text.
            singles = [x for x in page.words if x.fill and len(x.text) == 1 and abs(x.y - w.y) <= ROW_TOLERANCE]
            for wi, vi in inner:
                if len(wi.text) == 1 and len(singles) >= 3:
                    continue
                out.append(LineValue(form, part, page.number, line, vi, wi.text, wi.x0, wi.x1, wi.y, inline=True))
    return out, issues


def read_boxes(page: Page) -> dict[str, list[Word]]:
    """Entries typed into an information return's boxes (W-2, W-2G, 1099-R): each fill word belongs to the box whose printed
    number or letter is nearest above it and not to its right."""
    # Letters label boxes only in the form's left column (a-f on a W-2); elsewhere they are the vertical "Code" of box 12.
    labels = [w for w in page.words if not w.fill and LINE_LABEL.match(w.text) and (not w.text.isalpha() or w.x0 < 200)]
    boxes: dict[str, list[Word]] = {}
    for w in page.words:
        if not w.fill:
            continue
        above = [lab for lab in labels if 0 < lab.y - w.y <= 26 and lab.x0 <= w.x0 + 8]
        if not above:
            boxes.setdefault("?", []).append(w)
            continue
        # The box is the column the entry sits in (the rightmost label to its left), then the nearest label above in it.
        column = max(round(lab.x0) for lab in above)
        lab = min((lab for lab in above if round(lab.x0) >= column - 6), key=lambda lab: lab.y - w.y)
        boxes.setdefault(lab.text, []).append(w)
    return boxes


# ------------------------------------------------------------------------------------------------------------------ the extraction of one PDF

@dataclass
class Extraction:
    scenario: int
    path: Path
    sha256: str
    pages: list[Page]
    forms: list[tuple[str, int]]                      # (form, part) per page
    lines: list[LineValue] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)
    used: set[int] = field(default_factory=set)

    def page(self, n: int) -> Page:
        self.used.add(n)
        return self.pages[n - 1]

    def pages_of(self, form: str) -> list[int]:
        return [i + 1 for i, (f, _) in enumerate(self.forms) if f == form]

    def cover_text(self) -> str:
        """The cover page's typed text in content order (the order the IRS wrote it), whitespace collapsed."""
        p = self.page(1)
        return re.sub(r"\s+", " ", " ".join(t for _, t, _, fill in p.runs if fill))


def extract(path: Path, scenario: int) -> Extraction:
    from pypdf import PdfReader

    data = path.read_bytes()
    reader = PdfReader(path)
    pages = [read_page(reader, p, i + 1) for i, p in enumerate(reader.pages)]
    forms = [classify(p) for p in pages]
    ex = Extraction(scenario, path, hashlib.sha256(data).hexdigest(), pages, forms)
    for p, (form, part) in zip(pages, forms):
        if form not in LINE_FORMS or (LINE_FORMS[form] is not None and part not in LINE_FORMS[form]):
            continue
        if form == "f1040" and part == 1:
            # Page 1 above the Income section holds the names, address, filing status and dependents, read by the scenario.
            p = Page(p.number, [w for w in p.words if w.y < 345], p.runs, p.unmapped, p.repaired)
        values, issues = read_lines(p, form, part)
        ex.lines.extend(values)
        ex.issues.extend(issues)
    return ex


# ------------------------------------------------------------------------------------------------------------------ building a fixture

MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
               "December"]
FILING_STATUS = {"Single": "single", "Married filing jointly": "mfj", "Married filing separately": "mfs",
                 "Head of household": "hoh", "Qualifying surviving spouse": "qss"}
NO_EFFECT = "none: the engine already assumes it"
METADATA = "filing data, not part of the computation (the MeF return, T2-01, and its signatures, T2-03)"
STATE = "state return data: no state is computed (T1-05)"


def iso_date(text: str) -> str:
    """'February 7, 1986' or '02/15/2026' as 1986-02-07 / 2026-02-15."""
    m = re.fullmatch(r"(\w+) (\d{1,2}), (\d{4})", text.strip())
    if m:
        return date(int(m.group(3)), MONTH_NAMES.index(m.group(1)) + 1, int(m.group(2))).isoformat()
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text.strip())
    if m:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat()
    raise ValueError(f"not a date: {text!r}")


def ssn(text: str) -> str:
    digits = re.sub(r"\D", "", text)
    if len(digits) != 9:
        raise ValueError(f"not an SSN: {text!r}")
    return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"


def joined(words: list[Word]) -> str:
    """Words of one entry in reading order, joined without a space where they touch (a number split across fonts)."""
    ws = sorted(words, key=lambda w: (-round(w.y), w.x0))
    if not ws:
        return ""
    text = ws[0].text
    for prev, w in zip(ws, ws[1:]):
        text += ("" if abs(w.y - prev.y) < 3 and w.x0 - prev.x1 < 1.2 else " ") + w.text
    return text


class MissingFact(Exception):
    pass


class Builder:
    """Collects one scenario's fixture: every value comes from the PDF through these methods, with its page and source."""

    def __init__(self, ex: Extraction):
        self.ex = ex
        self.ret: dict[str, Any] = {"tax_year": TAX_YEAR}
        self.provenance: list[dict[str, Any]] = []
        self.unmodelled: list[dict[str, Any]] = []
        self.assumptions: list[dict[str, Any]] = []
        self.ambiguities: list[dict[str, Any]] = []
        self.consumed: dict[tuple[str, int, str, str | None], str] = {}
        self.aliases: dict[tuple[str, str], tuple[str, str]] = {}
        self.cover = [t for _, _, t in fill_lines(ex.page(1))]
        self.cover_used: set[int] = set()

    # -- provenance
    def src(self, path: str, page: int, source: str) -> None:
        self.ex.used.add(page)
        self.provenance.append({"path": path, "page": page, "source": source})

    def fact(self, pattern: str, *, required: bool = True) -> re.Match[str] | None:
        """A statement on the cover page (its lines joined, so a sentence may wrap)."""
        text = " ".join(self.cover)
        m = re.search(pattern, text)
        if m is None:
            if required:
                raise MissingFact(f"scenario {self.ex.scenario}: the cover does not say {pattern!r}")
            return None
        start = 0
        for i, line in enumerate(self.cover):   # mark the cover lines the match spans as accounted for
            end = start + len(line)
            if start <= m.end() and end >= m.start():
                self.cover_used.add(i)
            start = end + 1
        return m

    def unmodelled_fact(self, fact: str, page: int, effect: str) -> None:
        self.ex.used.add(page)
        self.unmodelled.append({"fact": fact, "page": page, "effect": effect})

    def cover_unmodelled(self, pattern: str, effect: str) -> None:
        m = self.fact(pattern)
        assert m is not None
        self.unmodelled_fact(m.group(0).strip(), 1, effect)

    def assume(self, path: str, value: Any, reason: str, alternatives: list[Any] | None = None, page: int | None = None) -> Any:
        entry: dict[str, Any] = {"path": path, "value": value, "reason": reason}
        if alternatives:
            entry["alternatives"] = alternatives
        if page:
            entry["page"] = page
        self.assumptions.append(entry)
        return value

    def ambiguity(self, page: int, note: str) -> None:
        self.ex.used.add(page)
        self.ambiguities.append({"page": page, "note": note})

    def read_row(self, page: int, y: float) -> None:
        """The scenario reads the amounts on this row itself (a table row with no line number): not an unread amount."""
        self.ex.issues = [i for i in self.ex.issues if not (i["page"] == page and abs(i["y"] - y) < 3)]

    # -- lines and boxes
    def line(self, form: str, line: str, *, column: str | None = None, path: str = "", page: int | None = None,
             alias: str | None = None, note: str = "") -> int | None:
        """A numbered line's amount, marked as an input of the fixture (its expected entry becomes role: input)."""
        found = [v for v in self.ex.lines if v.form == form and v.line == line and v.column == column and not v.inline
                 and (page is None or v.page == page)]
        if not found:
            return None
        if len(found) > 1:
            self.ambiguity(found[0].page, f"{form} line {line} is typed {len(found)} times; the first is used")
        v = found[0]
        self.consumed[(v.form, v.page, v.line, v.column)] = path
        if alias:
            self.aliases[(form, line)] = (alias, note)
        self.src(path, v.page, f"{form} line {line}{'.' + column if column else ''}")
        return v.value

    def boxes(self, page: int) -> dict[str, str]:
        return {k: joined(ws) for k, ws in read_boxes(self.ex.page(page)).items()}

    def text(self, page: int, x0: float = 0, x1: float = 620, y0: float = 0, y1: float = 800) -> list[str]:
        """Typed text inside a rectangle of the page, one string per baseline, top to bottom."""
        p = self.ex.page(page)
        inside = Page(p.number, [w for w in p.words if w.fill and x0 <= w.x0 < x1 and y0 <= w.y <= y1], [], 0, 0)
        return [t for _, _, t in fill_lines(inside)]

    def box_amount(self, bx: dict[str, str], box: str) -> int | None:
        if box not in bx:
            return None
        values = [parse_amount(t) for t in bx[box].split()]
        amounts = [v for v in values if v is not None]
        return amounts[0] if len(amounts) == 1 else None

    # -- forms
    def checked(self, page: int, y0: float, y1: float, x0: float = 0, x1: float = 620) -> list[str]:
        """The printed option right after each check mark in a band of the page (a box precedes its label)."""
        p = self.ex.page(page)
        out = []
        for c in sorted((w for w in p.words if w.text == CHECK and y0 <= w.y <= y1 and x0 <= w.x0 <= x1), key=lambda w: (-w.y, w.x0)):
            right = sorted((w for w in p.words if not w.fill and abs(w.y - c.y) <= 4 and w.x0 >= c.x1 - 1), key=lambda w: w.x0)
            out.append(" ".join(w.text for w in right[:6]))
        return out

    def filing_status(self, page: int = 2) -> str | None:
        options = self.checked(page, 540, 600, 80, 130)
        for text in options:
            for label, status in FILING_STATUS.items():
                if text.startswith(label):
                    self.src("filing_status", page, f"Form 1040 filing status checked: {label}")
                    return status
        return None

    def header(self, page: int = 2) -> dict[str, Any]:
        """The name lines of Form 1040 page 1: first names, last names and SSNs of the taxpayer and the spouse."""
        p = self.ex.page(page)
        rows: dict[str, list[Word]] = {"taxpayer": [], "spouse": []}
        for w in p.words:
            if w.fill and 680 <= w.y <= 694:
                rows["taxpayer"].append(w)
            elif w.fill and 656 <= w.y <= 670:
                rows["spouse"].append(w)
        out: dict[str, Any] = {}
        for who, ws in rows.items():
            if not ws:
                continue
            ws.sort(key=lambda w: w.x0)
            digits = "".join(w.text for w in ws if re.fullmatch(r"[\d-]+", w.text))
            names = [w for w in ws if not re.fullmatch(r"[\d-]+", w.text)]
            first = [w.text for w in names if w.x0 < 250]
            last = [w.text for w in names if w.x0 >= 250]
            if not last and len(first) >= 2:          # one string typed across both name fields
                first, last = first[:-1], first[-1:]
            out[who] = {"first_name": " ".join(first).title(), "last_name": " ".join(last).title(), "ssn": ssn(digits)}
        return out

    def dependents(self, page: int = 2) -> list[dict[str, Any]]:
        """Form 1040 page 1 dependents table: one dict per column with first and last name, SSN and relationship."""
        p = self.ex.page(page)
        anchors = {w.text: w.y for w in p.words if not w.fill and w.x0 < 145 and 350 < w.y < 500
                   and w.text in ("(1)", "(2)", "(3)", "(4)")}
        if len(anchors) < 4:
            return []
        fields = {"(1)": "first_name", "(2)": "last_name", "(3)": "ssn", "(4)": "relationship"}
        cols: dict[int, dict[str, list[str]]] = {}
        for w in p.words:
            if not w.fill or w.x0 < 140:
                continue
            for label, y in anchors.items():
                if abs(w.y - y) <= 5:
                    col = 0 if w.x0 < 252 else 1 if w.x0 < 360 else 2 if w.x0 < 468 else 3
                    cols.setdefault(col, {}).setdefault(fields[label], []).append(w.text)
        out = []
        for col in sorted(cols):
            d = {k: " ".join(v) for k, v in cols[col].items()}
            if "ssn" in d:
                d["ssn"] = ssn(d["ssn"])
            d["first_name"] = d.get("first_name", "").title()
            d["last_name"] = d.get("last_name", "").title()
            d["relationship"] = d.get("relationship", "").lower()
            out.append(d)
            self.src(f"dependents[{col}]", page, "Form 1040 dependents table, columns (1)-(4)")
        return out

    def w2(self, page: int, owners: dict[str, str]) -> dict[str, Any]:
        """A Form W-2 as the model's W2: boxes 1-6, 10, 12 (code and amount), 13 retirement plan, 14b; state boxes are
        recorded as unmodelled. `owners` maps an SSN to taxpayer or spouse."""
        bx = read_boxes(self.ex.page(page))
        text = {k: joined(ws) for k, ws in bx.items()}
        employee = ssn(text["a"])
        owner = owners.get(employee)
        if owner is None:
            raise MissingFact(f"W-2 on page {page}: employee SSN {employee} is neither the taxpayer's nor the spouse's")
        n = len(self.ret.setdefault("w2s", []))
        path = f"w2s[{n}]"
        employer = self.text(page, 30, 330, 640, 698)   # box c: the employer's name, then its address
        w2: dict[str, Any] = {"owner": owner, "employer_name": employer[0] if employer else "", "employer_ein": text.get("b", "")}
        self.src(f"{path}.employer_name", page, "W-2 box c, first line")
        self.src(f"{path}.employer_ein", page, "W-2 box b")
        for box, key in (("1", "wages"), ("2", "federal_withholding"), ("3", "ss_wages"), ("4", "ss_tax"),
                         ("5", "medicare_wages"), ("6", "medicare_tax"), ("7", "ss_tips"), ("10", "dependent_care_benefits")):
            value = self.box_amount(text, box)
            if value is not None:
                w2[key] = value
                self.src(f"{path}.{key}", page, f"W-2 box {box}")
        entries = sorted((w for k, ws in bx.items() if re.fullmatch(r"12[a-d]?", k) for w in ws), key=lambda w: (-w.y, w.x0))
        codes: dict[str, int] = {}
        for w in entries:
            if re.fullmatch(r"[A-Z]{1,2}", w.text):
                amount = [x for x in entries if abs(x.y - w.y) < 3 and parse_amount(x.text) is not None]
                if len(amount) == 1:
                    codes[w.text] = parse_amount(amount[0].text) or 0
        if codes:
            w2["box12"] = codes
            self.src(f"{path}.box12", page, "W-2 box 12 (code and amount)")
        if "14b" in text and re.fullmatch(r"\d{2,3}", text["14b"]):
            w2["tipped_occupation_code"] = int(text["14b"])
            self.src(f"{path}.tipped_occupation_code", page, "W-2 box 14b (Treasury tipped occupation code)")
        retirement = [c for c in self.checked(page, 580, 605, 340, 460)]
        if retirement:
            self.ambiguity(page, f"W-2 box 13 has check marks ({retirement}); box 13 is not read")
        state = " ".join(text.get(k, "") for k in ("15", "16", "17") if text.get(k))
        if state:
            self.unmodelled_fact(f"W-2 from {w2['employer_name']}: state boxes 15-17 ({state})", page, STATE)
        self.ret["w2s"].append(w2)
        return w2

    def dob_from_cover(self, who: str = r"Taxpayer'?s") -> str:
        m = self.fact(who + r" Date of Birth is (\w+ \d{1,2}, \d{4})")
        assert m is not None
        return iso_date(m.group(1))


def _single_by_elimination(b: Builder, page: int = 2) -> None:
    b.ret["filing_status"] = b.assume(
        "filing_status", "single",
        "no filing status is checked on Form 1040; the return names no spouse and no dependent, which leaves Single "
        "(married filing separately would name the spouse, head of household and qualifying surviving spouse need a "
        "qualifying person)", page=page)


def _taxpayer(b: Builder, page: int = 2) -> dict[str, Any]:
    h = b.header(page)
    t = h["taxpayer"]
    b.src("taxpayer", page, "Form 1040 page 1 name and SSN")
    cover_ssn = b.fact(r"SSN:\s*(\d{3}-\d{2}-\d{4})")
    assert cover_ssn is not None
    if cover_ssn.group(1) != t["ssn"]:
        b.ambiguity(1, f"the cover gives SSN {cover_ssn.group(1)}, Form 1040 page 1 {t['ssn']}")
    b.fact(r"Taxpayers?:\s*[A-Za-z ]+?(?= SSN:|$)", required=False)
    b.fact(r"ATS Test\s+Scenario\s*\d+", required=False)
    b.fact(r"Test Scenario \d+ includes the following forms?:", required=False)
    return {"first_name": t["first_name"], "last_name": t["last_name"], "ssn": t["ssn"]}


# Each scenario reads its PDF through the Builder. Comments cite what the PDF says; nothing is computed.

def scenario_1(b: Builder) -> None:
    _single_by_elimination(b)
    b.ret["taxpayer"] = _taxpayer(b)
    b.ambiguity(1, "the taxpayer's date of birth is not stated: the engine treats her as under 65 and not blind")
    b.fact(r"Taxpayer is a U\.S\. citizen\.")
    b.ret["citizen_or_qualified_alien"] = True
    b.src("citizen_or_qualified_alien", 1, "cover: Taxpayer is a U.S. citizen")
    owners = {b.ret["taxpayer"]["ssn"]: "taxpayer"}
    for page in b.ex.pages_of("w2"):
        b.w2(page, owners)
    sch_h = b.ex.pages_of("sch_h")[0]
    h_lines = {v.line: v.value for v in b.ex.lines if v.form == "sch_h"}
    b.unmodelled_fact(f"Schedule H: cash wages of a household employee subject to social security and Medicare tax "
                      f"(lines 1 and 3: {h_lines.get('1')}, {h_lines.get('3')}), federal income tax withheld (line 7: "
                      f"{h_lines.get('7')}); employer identification number on the form", sch_h,
                      "changes the return: Schedule H's taxes (Schedule 2 line 9) are not computed; the model takes them as an "
                      "entered amount (household_employment_taxes), which the scenario does not state")
    b.cover_unmodelled(r"Taxpayer'?s Tax Year 2025 carryforward credit on Form 5695 line 1 is \$[\d,]+\.",
                       "changes the return: Form 5695 is not modelled, so the carried-forward credit (Schedule 3 line 5) is "
                       "not claimed")
    b.cover_unmodelled(r"Assume Form 1062 and Form 1062 Schedule A are attached\.",
                       "changes the return: Form 1062 (IRC §1062 deferral) is not modelled; Form 1040 line 24b prints its "
                       "amount")


def scenario_2(b: Builder) -> None:
    h = b.header()
    b.ret["filing_status"] = b.assume(
        "filing_status", "mfj",
        "no filing status is checked; the spouse is named on Form 1040's joint-return line and the cover speaks of the "
        "Taxpayer and Spouse", page=2)
    b.ret["taxpayer"] = {**{k: h["taxpayer"][k] for k in ("first_name", "last_name", "ssn")},
                         "dob": b.dob_from_cover(r"Primary Taxpayer'?s")}
    b.ret["spouse"] = {**{k: h["spouse"][k] for k in ("first_name", "last_name", "ssn")},
                       "dob": b.dob_from_cover(r"Secondary Taxpayer'?s")}
    b.src("taxpayer.dob", 1, "cover: Primary Taxpayer's Date of Birth")
    b.src("spouse.dob", 1, "cover: Secondary Taxpayer's Date of Birth")
    _taxpayer(b)
    b.fact(r"The Taxpayer\s+and Spouse are U\.S\. citizens\.")
    b.ret["citizen_or_qualified_alien"] = True
    deps = b.dependents()
    dob = b.dob_from_cover(r"Dependent'?s")
    b.fact(r"The Dependent is a full time high school student\.")
    d = deps[0]
    b.ret["dependents"] = [{"first_name": d["first_name"], "last_name": d["last_name"], "ssn": d["ssn"], "dob": dob,
                            "relationship": d["relationship"], "full_time_student": True,
                            "months_in_home": b.assume("dependents[0].months_in_home", 12,
                                                       "Form 1040's column (5) (lived with you more than half of 2026) is "
                                                       "not checked in this input-only scenario; any value over 6 months "
                                                       "makes the son a qualifying child", alternatives=[7], page=2)}]
    b.src("dependents[0].dob", 1, "cover: Dependent's Date of Birth")
    b.src("dependents[0].full_time_student", 1, "cover: full time high school student")
    owners = {b.ret["taxpayer"]["ssn"]: "taxpayer", b.ret["spouse"]["ssn"]: "spouse"}
    for page in b.ex.pages_of("w2"):
        b.w2(page, owners)
    itemized: dict[str, Any] = {}
    for line, key in (("5a", "state_local_income_tax"), ("5b", "real_estate_tax"), ("8a", "mortgage_interest_1098"),
                      ("8c", "points_not_on_1098"), ("8d", "mortgage_insurance_premiums"), ("11", "charity_cash")):
        value = b.line("sch_a", line, path=f"itemized.{key}")
        if value is not None:
            itemized[key] = value
    f8283 = b.ex.pages_of("f8283")[0]
    row = [t for _, _, t in fill_lines(b.ex.page(f8283)) if re.match(r"\d{1,2}/\d{1,2}/\d{4}", t)]
    m = re.match(r"(\d{1,2}/\d{1,2}/\d{4}) (\w+) (\w+) ([\d,]+) ([\d,]+) (.+)", row[0]) if row else None
    if m:
        itemized["charity_noncash"] = parse_amount(m.group(5))
        b.src("itemized.charity_noncash", f8283, "Form 8283 Section A line 1, column (h) fair market value")
        b.unmodelled_fact(f"Form 8283 Section A: donee Goodwill, clothes and toys, contributed {m.group(1)}, acquired "
                          f"{m.group(2)} by {m.group(3)}, donor's cost {m.group(4)}, fair market value {m.group(5)} by "
                          f"{m.group(6)}", f8283, "filing data: Form 8283 is not modelled; its fair market value enters "
                          "Schedule A line 12 (charity_noncash)")
    else:
        b.ambiguity(f8283, "Form 8283 Section A row not read")
    b.ret["itemized"] = itemized
    b.ambiguity(b.ex.pages_of("sch_a")[0], "Schedule A is attached, but the scenario does not say whether to itemize; "
                "the entries total less than the 2026 standard deduction for a joint return, and the engine takes the "
                "larger of the two")
    business: dict[str, Any] = {"owner": "taxpayer", "expenses": {}}
    sch_c = b.ex.pages_of("sch_c")[0]
    business["name"] = b.text(sch_c, 0, 400, 655, 675)[0]
    business["principal_business_code"] = re.sub(r"\D", "", " ".join(b.text(sch_c, 400, 620, 655, 675)))
    b.src("businesses[0].name", sch_c, "Schedule C line A, principal business or profession (line C, the business name, is "
                                       "blank)")
    b.src("businesses[0].principal_business_code", sch_c, "Schedule C line B")
    gross = b.line("sch_c", "1", path="businesses[0].gross_receipts")
    if gross is None:
        business["gross_receipts"] = b.assume("businesses[0].gross_receipts", 0,
                                              "Schedule C line 1 (gross receipts) is blank in this input-only scenario; no "
                                              "receipts are stated", page=sch_c)
    for line, key in (("8", "advertising"), ("9", "car_truck"), ("19", "pension_profit_sharing"), ("22", "supplies"),
                      ("23", "taxes_licenses")):
        value = b.line("sch_c", line, path=f"businesses[0].expenses.{key}")
        if value is not None:
            business["expenses"][key] = value
    b.line("sch_c", "30", path="businesses[0] (line 30 is 0: no business use of the home, the model's default)")
    b.ret["businesses"] = [business]
    p2 = b.ex.pages_of("sch_c")[1]
    vehicle = " | ".join(t for y, x, t in fill_lines(b.ex.page(p2)) if 380 < y < 440)
    b.unmodelled_fact(f"Schedule C Part IV vehicle information (line 43 date placed in service; lines 44a-44c business, "
                      f"commuting and other miles): {vehicle}", p2,
                      "the engine takes car and truck expenses as the amount on line 9; it does not figure them from miles")
    b.cover_unmodelled(r"Assume all mileage occurred before July 1, 2026 on Schedule C, Part IV, Line 44a\.",
                       "the engine takes car and truck expenses as the amount on Schedule C line 9")
    m300 = b.fact(r"Taxpayer paid an estimated tax payment of \$([\d,.]+) in 2026 \(applied from 2025 return\)\.")
    assert m300 is not None
    b.ret["payments"] = {"prior_year_overpayment_applied": int(float(m300.group(1).replace(",", "")))}
    b.src("payments.prior_year_overpayment_applied", 1, "cover: estimated tax payment applied from the 2025 return")
    b.cover_unmodelled(r"The Taxpayers are patrons in a specified agricultural cooperative; therefore, they do not "
                       r"qualify for the Qualified Business Income Deduction",
                       "changes the return when Schedule C has qualified business income: the engine has no input that "
                       "withholds the deduction, and IRC §199A(g) is not modelled (here Schedule C is a loss, so no "
                       "deduction is figured)")
    b.cover_unmodelled(r"Spouse Identity Protection PIN is \d+\.", METADATA)
    p1040 = b.ex.pages_of("f1040")[1]
    preparer = " | ".join(t for y, x, t in fill_lines(b.ex.page(p1040)) if y < 120)
    b.unmodelled_fact(f"Form 1040 signature area: spouse IP PIN and the paid preparer's block ({preparer})", p1040, METADATA)


def scenario_3(b: Builder) -> None:
    _single_by_elimination(b)
    b.ret["taxpayer"] = {**_taxpayer(b), "dob": b.dob_from_cover()}
    b.src("taxpayer.dob", 1, "cover: Taxpayer's Date of Birth")
    b.fact(r"Taxpayer is a U\.S\s*\.\s*citizen\.")
    b.ret["citizen_or_qualified_alien"] = True
    page = b.ex.pages_of("f1099r")[0]
    bx = b.boxes(page)
    payer = b.text(page, 0, 250, 715, 740)[0]
    code = bx.get("7a", bx.get("7", ""))
    b.ret["retirement"] = [{"payer": payer, "gross_distribution": b.box_amount(bx, "1"),
                            "taxable_amount": b.box_amount(bx, "2a"), "federal_withholding": b.box_amount(bx, "4"),
                            "distribution_code": code.strip(), "ira_sep_simple": False}]
    for key, box in (("gross_distribution", "1"), ("taxable_amount", "2a"), ("federal_withholding", "4"), ("distribution_code", "7")):
        b.src(f"retirement[0].{key}", page, f"Form 1099-R box {box}")
    b.src("retirement[0].ira_sep_simple", page, "Form 1099-R: the IRA/SEP/SIMPLE box is not checked")
    m = b.fact(r"Taxable refund amount is \$([\d,]+)\.")
    assert m is not None
    b.ret["state_refund_taxable"] = parse_amount(m.group(1))
    b.src("state_refund_taxable", 1, "cover: Taxable refund amount")
    transactions = []
    for term, line in (("short", "1a"), ("long", "8a")):
        proceeds = b.line("sch_d", line, column="d", path=f"capital_transactions[{len(transactions)}].proceeds")
        basis = b.line("sch_d", line, column="e", path=f"capital_transactions[{len(transactions)}].cost_basis")
        if proceeds is None or basis is None:
            continue
        n = len(transactions)
        transactions.append({"description": f"Schedule D line {line} totals (Form 1099-B, basis reported to the IRS, no "
                                            f"adjustments)", "term": term, "proceeds": proceeds, "cost_basis": basis,
                             "basis_reported_to_irs": True, "form_1099_received": True,
                             "sold": b.assume(f"capital_transactions[{n}].sold", "2026-12-31",
                                              f"Schedule D line {line} prints totals without sale dates; the model needs a "
                                              f"date, and with the term stated ({term}-term) the date does not enter the "
                                              f"computation", alternatives=["2026-01-02"], page=b.ex.pages_of("sch_d")[0])})
    b.ret["capital_transactions"] = transactions
    for form, why in (("sch_f", "Schedule F"), ("f4835", "Form 4835")):
        page_f = b.ex.pages_of(form)[0]
        values = ", ".join(f"line {v.line} {v.value}" for v in b.ex.lines if v.form == form)
        activity = ""
        if form == "sch_f":
            row = sorted((w for w in b.ex.page(page_f).words if w.fill and 655 < w.y < 672), key=lambda w: w.x0)
            crop = " ".join(w.text for w in row if re.search(r"[A-Za-z]", w.text))
            code = "".join(w.text for w in row if re.fullmatch(r"\d", w.text))
            activity = f"principal crop or activity (line A) {crop!r}, code (line B) {code}; "
        b.unmodelled_fact(f"{why}: {activity}{values}", page_f,
                          f"changes the return: {why} is not modelled, so its income and expenses (and any self-employment "
                          f"tax on them) are missing from the engine's return")
    b.cover_unmodelled(r"Taxpayer elects the Farm Optional Method on Schedule SE\.",
                       "changes the return: Schedule SE's optional methods are not modelled")
    b.cover_unmodelled(r"Taxpayer is a patron in a specified agricultural cooperative\.",
                       "changes the return when there is qualified business income: IRC §199A(g) is not modelled")
    b.cover_unmodelled(r"Taxpayer elects not to income average\.", NO_EFFECT + " (Schedule J is not modelled)")
    b.cover_unmodelled(r"Taxpayer did not invest in a Qualified Opportunity Fund \(QOF\)\.", NO_EFFECT)
    b.cover_unmodelled(r"Identity Protection PIN\s*:\s*\d+", METADATA)


def scenario_4(b: Builder) -> None:
    b.ret["filing_status"] = b.assume(
        "filing_status", "hoh",
        "no filing status is checked; the cover and the forms describe an unmarried parent (no spouse named) whose two "
        "children lived with her all year (Schedule EIC line 6: 12 months) and who paid their care; whether she is "
        "considered unmarried and paid over half the cost of the home is not stated", alternatives=["single"], page=2)
    b.ret["taxpayer"] = {**_taxpayer(b), "dob": b.dob_from_cover(), "blind": True, "full_time_student": True}
    b.src("taxpayer.dob", 1, "cover: Taxpayer's Date of Birth")
    b.fact(r"Taxpayer is legally blind")
    b.src("taxpayer.blind", 1, "cover: Taxpayer is legally blind")
    b.fact(r"Taxpayer\s+is a full-time student\.")
    b.src("taxpayer.full_time_student", 1, "cover: Taxpayer is a full-time student")
    b.fact(r"Taxpayer is a U\.S\. citizen")
    b.ret["citizen_or_qualified_alien"] = True
    deps = b.dependents()
    dobs = [iso_date(b.fact(rf"{n} Dependent Date of Birth is (\w+ \d{{1,2}}, \d{{4}})").group(1))  # type: ignore[union-attr]
            for n in ("1st", "2nd")]
    eic = b.ex.pages_of("sch_eic")[0]
    eic_lines = {round(y): t for y, x, t in fill_lines(b.ex.page(eic))}
    months = [t for y, t in eic_lines.items() if 60 < y < 80]
    years = [t for y, t in eic_lines.items() if 320 < y < 340]
    out = []
    for i, d in enumerate(deps):
        out.append({"first_name": d["first_name"], "last_name": d["last_name"], "ssn": d["ssn"], "dob": dobs[i],
                    "relationship": d["relationship"], "months_in_home": int(months[0].split()[i])})
        b.src(f"dependents[{i}].dob", 1, f"cover: {('1st', '2nd')[i]} Dependent Date of Birth")
        b.src(f"dependents[{i}].months_in_home", eic, "Schedule EIC line 6")
    year_digits = re.sub(r"\s", "", years[0]) if years else ""
    if year_digits != "".join(d[:4] for d in dobs):
        b.ambiguity(eic, f"Schedule EIC line 3 years ({years}) do not match the cover's dates of birth {dobs}")
    b.ret["dependents"] = out
    owners = {b.ret["taxpayer"]["ssn"]: "taxpayer"}
    for page in b.ex.pages_of("w2"):
        b.w2(page, owners)
    f2441 = b.ex.pages_of("f2441")[0]
    p = b.ex.page(f2441)
    providers = [w for w in p.words if w.fill and abs(w.y - 434) < 4 and parse_amount(w.text) is not None]
    persons = [w for w in p.words if w.fill and 255 < w.y < 290 and w.x0 > 480 and parse_amount(w.text) is not None]
    paid = sum(parse_amount(w.text) or 0 for w in providers)
    incurred = sum(parse_amount(w.text) or 0 for w in persons)
    if paid != incurred:
        b.ambiguity(f2441, f"Form 2441: the providers were paid {paid} (line 1e) but the qualifying persons' expenses total "
                           f"{incurred} (line 2, column (c))")
    b.ret["dependent_care_expenses"] = incurred
    b.ret["dependent_care_qualifying_persons"] = len(persons)
    b.src("dependent_care_expenses", f2441, "Form 2441 line 2, column (c), both qualifying persons")
    b.src("dependent_care_qualifying_persons", f2441, "Form 2441 line 2, one row per qualifying person")
    b.unmodelled_fact("Form 2441 line 1: the care providers' names, addresses and identifying numbers", f2441, METADATA)
    m = b.fact(r"The Adjusted Qualified Education Expenses are \$([\d,]+) on\s+Form 8863\.")
    assert m is not None
    b.ret["students"] = [{"name": f"{b.ret['taxpayer']['first_name']} {b.ret['taxpayer']['last_name']}",
                          "ssn": b.ret["taxpayer"]["ssn"], "qualified_expenses": parse_amount(m.group(1))}]
    b.src("students[0].qualified_expenses", 1, "cover: Adjusted Qualified Education Expenses on Form 8863")
    b.assume("students[0].aotc_years_claimed", 0,
             "the American opportunity credit is claimed (Form 8862 Part IV is completed for the student), but the years "
             "it was claimed before are not stated; the model's default is 0", alternatives=[4])
    f8863 = b.ex.pages_of("f8863")[-1]
    school = " | ".join(t for y, x, t in fill_lines(b.ex.page(f8863)) if 400 < y < 610)
    b.unmodelled_fact(f"Form 8863 Part III: the educational institution ({school})", f8863, METADATA)
    for page in b.ex.pages_of("f8862"):
        answers = [a.split()[0] if a else "?" for a in b.checked(page, 0, 700)]
        b.unmodelled_fact(f"Form 8862 page {b.ex.forms[page - 1][1]}: {len(answers)} questions answered, in page order: "
                          f"{', '.join(answers)}", page,
                          "a filing requirement: the engine neither asks for Form 8862 nor attaches it; it figures the "
                          "credits the same way")
    b.cover_unmodelled(r"The taxpayer has \$[\d,]+ in moving expenses\.",
                       "changes the return only for a member of the Armed Forces (Form 3903), which the scenario does not "
                       "state; the model has no moving expense input")
    b.cover_unmodelled(r"The taxpayer is not a bona fide resident of Puerto Rico\.", NO_EFFECT)
    sch3a = b.ex.pages_of("sch_3a")[0]
    answers = b.checked(sch3a, 280, 460)
    if any(a.startswith("Yes. Go to line 8") for a in answers):
        b.ret["want_federal_public_benefit"] = True
        b.src("want_federal_public_benefit", sch3a, "Schedule 3-A line 7: Yes")


def scenario_5(b: Builder) -> None:
    _single_by_elimination(b)
    b.ret["taxpayer"] = {**_taxpayer(b), "dob": b.dob_from_cover()}
    b.src("taxpayer.dob", 1, "cover: Taxpayer's Date of Birth")
    b.fact(r"Taxpayer is a U\.S\. citizen\.")
    b.ret["citizen_or_qualified_alien"] = True
    owners = {b.ret["taxpayer"]["ssn"]: "taxpayer"}
    for page in b.ex.pages_of("w2"):
        b.w2(page, owners)
    f8888 = b.ex.pages_of("f8888")[0]
    numbers = [re.sub(r"\D", "", t) for t in b.text(f8888, 140, 380, 430, 630)]
    numbers = [n for n in numbers if n]
    types = [t.split()[0] for t in b.checked(f8888, 430, 630)]
    accounts = "; ".join(f"line {i + 1}: routing {numbers[2 * i]}, account {numbers[2 * i + 1]}, {types[i] if i < len(types) else '?'}"
                         for i in range(len(numbers) // 2))
    b.cover_unmodelled(r"Allocate the Taxpayer'?s refund on Form 8888 as follows, \$1,000 into saving\s*account\s*and the "
                       r"remainder of refund should be deposited in\s*to\s*the checking account\.",
                       "filing data: the engine figures the refund (Form 1040 line 35a) but has no direct deposit or Form "
                       "8888 model")
    b.unmodelled_fact(f"Form 8888 lines 1b-2d ({accounts}); lines 1a and 2a, the amounts, are blank", f8888, METADATA)


def scenario_6(b: Builder) -> None:
    _single_by_elimination(b)
    b.ret["taxpayer"] = {**_taxpayer(b), "dob": b.dob_from_cover(), "can_be_claimed_as_dependent": True}
    b.src("taxpayer.dob", 1, "cover: Taxpayer's Date of Birth")
    b.fact(r"Taxpayer can be claimed as a dependent on parents federal tax return\.")
    b.src("taxpayer.can_be_claimed_as_dependent", 1, "cover: can be claimed as a dependent on parents' return")
    b.fact(r"Taxpayer is a U\.S\. citizen\.")
    b.ret["citizen_or_qualified_alien"] = True
    owners = {b.ret["taxpayer"]["ssn"]: "taxpayer"}
    w2_page = b.ex.pages_of("w2")[0]
    w2 = b.w2(w2_page, owners)
    if "ss_tax" in w2 and "ss_wages" not in w2:
        b.ambiguity(w2_page, f"W-2 boxes 3 and 5 (social security and Medicare wages) are blank while boxes 4 and 6 show "
                             f"{w2.get('ss_tax')} and {w2.get('medicare_tax')} withheld; the blank boxes are entered as zero")
    w2g = b.ex.pages_of("w2g")[0]
    bx = b.boxes(w2g)
    winnings = b.box_amount(bx, "1")
    b.ret["other_income"] = {"8b": winnings}
    b.src("other_income.8b", w2g, "Form W-2G box 1 (reportable winnings), Schedule 1 line 8b")
    box4 = bx.get("4", "")
    withheld = [parse_amount(t) for t in box4.replace(UNMAPPED, " ").split() if parse_amount(t) is not None]
    if withheld != [0]:
        b.ambiguity(w2g, f"Form W-2G box 4 reads {box4!r}")
    if UNMAPPED in box4:
        b.ambiguity(w2g, "Form W-2G box 4: besides its 0, one glyph that no font of the PDF names is printed in the box; "
                         "the box is read as 0")
    if bx.get("20") == "26":
        b.ambiguity(w2g, "Form W-2G: '26' is typed at the right of the form's top row (the tax year); not read")
    payer = " | ".join(b.text(w2g, 0, 290, 500, 740))
    b.unmodelled_fact(f"Form W-2G: payer and winner block ({payer}); box 2 date won {bx.get('2')}; box 3 type of wager "
                      f"{bx.get('3')}; box 5 transaction {bx.get('5')}; box 9 winner's TIN {bx.get('9')}", w2g,
                      "the model has no W-2G document: the winnings enter Schedule 1 line 8b and box 4's withholding (0) "
                      "Form 1040 line 25b would take nothing")
    sch_e = b.ex.pages_of("sch_e")[1]
    p = b.ex.page(sch_e)
    name_row = [t for y, x, t in fill_lines(p) if 530 < y < 545]
    row = sorted((w for w in p.words if w.fill and 450 < w.y < 465 and parse_amount(w.text) is not None), key=lambda w: w.x0)
    headers = {w.text: w.x0 for w in p.words if not w.fill and 470 < w.y < 485 and re.fullmatch(r"\([g-k]\)", w.text)}
    entries = []
    for w in row:
        col = max((h for h, x in headers.items() if x <= w.x1 + 2), key=lambda h: headers[h], default=None)
        entries.append((col, parse_amount(w.text) or 0))
        b.read_row(sch_e, w.y)
    m = re.match(r"(.+?) P (\d{2}-\d{7})", name_row[0]) if name_row else None
    if not m:
        raise MissingFact("Schedule E line 28 row A not read")
    k1s = []
    for col, value in entries:
        amount = {"(i)": -value, "(k)": value, "(g)": -value, "(h)": value}.get(col or "")
        if amount is None:
            b.ambiguity(sch_e, f"Schedule E line 28 row A: {value} in column {col} is not read")
            continue
        n = len(k1s)
        k1s.append({"entity_name": m.group(1), "entity_ein": m.group(2), "entity_type": "partnership",
                    "passive": col in ("(g)", "(h)"), "ordinary_income": amount})
        b.src(f"k1s[{n}].ordinary_income", sch_e, f"Schedule E line 28 row A, column {col}")
    b.ret["k1s"] = k1s
    b.assume("k1s", "two entries for one partnership",
             "Schedule E line 28 row A prints a nonpassive loss allowed (column (i)) and nonpassive income (column (k)) "
             "for the same partnership without saying which Schedule K-1 boxes they come from; each enters as its own "
             "amount of ordinary income or loss", page=sch_e)
    b.cover_unmodelled(r"Taxpayer\s+is in a Partnership engaged in the trade or business of gambling\.",
                       "changes the return: the engine has no gambling loss limitation (IRC §165(d) as amended by P.L. "
                       "119-21 for 2026) and takes the partnership's amounts as stated on Schedule E")
    b.ambiguity(w2g, f"whether the W-2G's {winnings} of winnings is the taxpayer's own (Schedule 1 line 8b) or part of "
                     f"the gambling partnership's income on Schedule E (also {dict(entries).get('(k)')}) is not stated; "
                     f"both are entered as printed")


def scenario_7(b: Builder) -> None:
    b.ret = None  # type: ignore[assignment]  # a Form 4868 alone: there is no Form 1040 to compute
    _taxpayer_7 = b.fact(r"Taxpayer:\s*(.+?)\s+SSN:\s*(\d{3}-\d{2}-\d{4})")
    assert _taxpayer_7 is not None
    b.fact(r"ATS Test\s+Scenario\s*7")
    b.fact(r"Test Scenario 7 includes the following form:?")
    b.fact(r"Form 4868")
    b.fact(r"Payment Information:")
    for label in ("Routing Transit Number", "Bank Account Number", "Bank Account Type", "Payment Amount", "Payment Due Date",
                  "Taxpayer Phone Number"):
        b.cover_unmodelled(rf"{label}:\s*\S+", "Form 4868 is not modelled (backlog T2-05): the extension and its electronic "
                                               "payment are a separate submission, not part of a Form 1040")
    page = b.ex.pages_of("f4868")[0]
    typed = " | ".join(b.text(page, 0, 300, 0, 140))
    amounts = {v.line: v.value for v in b.ex.lines if v.form == "f4868"}
    b.unmodelled_fact(f"Form 4868 Part I (name, address, SSN): {typed}; Part II: {amounts} (line 6, the balance due, and "
                      f"line 7, the amount paid, are blank; the cover gives a $4,000 payment)", page,
                      "Form 4868 is not modelled (backlog T2-05)")
    if "4" in amounts and "5" in amounts:
        b.ambiguity(page, f"Form 4868 line 6 (balance due, line 4 less line 5 = {amounts['4'] - amounts['5']}) and line 7 "
                          f"(amount paying) are blank; the cover's payment of $4,000 is not printed on the form")


def scenario_12(b: Builder) -> None:
    status = b.filing_status()
    b.ret["filing_status"] = status
    b.ret["taxpayer"] = _taxpayer(b)
    b.ambiguity(1, "the taxpayer's date of birth is not stated (Form 1040 line 12d is not checked: born after January 1, "
                   "1962); the engine treats him as under 65")
    owners = {b.ret["taxpayer"]["ssn"]: "taxpayer"}
    for page in b.ex.pages_of("w2"):
        b.w2(page, owners)
    _schedule_c(b, {"1": "gross_receipts"}, {"8": "advertising", "11": "contract_labor", "15": "insurance",
                                             "17": "legal_professional", "18": "office", "20b": "rent_other", "22": "supplies",
                                             "23": "taxes_licenses", "27a": "energy_efficient_buildings", "27b": "other"})
    b.line("sch_c", "48", path="businesses[0].expenses.other", alias="27b",
           note="Part V total, which Schedule C line 27b repeats; the engine keeps line 27b only")
    _digital_assets(b)
    for form, why in (("f3800", "Form 3800 (general business credit, Schedule 3 line 6a)"), ("f3800_sch_a", "Schedule A (Form 3800)"),
                      ("f7207", "Form 7207 (advanced manufacturing production credit)"),
                      ("f7220", "Form 7220 (prevailing wage and apprenticeship)"),
                      ("f7205", "Form 7205 (energy efficient commercial buildings deduction, taken as stated on Schedule C "
                                "line 27a)"),
                      ("f4562b", "Form 4562-B (amortization of business startup costs, taken as stated on Schedule C line 27b)")):
        pages = b.ex.pages_of(form)
        if pages:
            b.unmodelled_fact(f"{why}: pages {pages}", pages[0], "changes the return: the form is not modelled" if form in
                              ("f3800", "f3800_sch_a", "f7207", "f7220") else "none for the amounts: the deduction enters as "
                              "the Schedule C amount; the form itself is not modelled")
    for item in ("Schedule A Form 3800\" signature", "Form 3800 Schedule A statement", "Form 7205 certification",
                 "Form 7207 Designer Allocation"):
        b.cover_unmodelled(r"Binary Attachment \(\"?" + re.escape(item) + r"\)", METADATA + "; binary PDF attachments")
    sch_c2 = b.ex.pages_of("sch_c")[1]
    part_v = [(y, t) for y, x, t in fill_lines(b.ex.page(sch_c2)) if 70 < y < 300]
    for y, _ in part_v:
        b.read_row(sch_c2, y)
    b.unmodelled_fact(f"Schedule C Part V (other expenses): {' | '.join(t for _, t in part_v)}", sch_c2,
                      "none: the total (line 48) enters as Schedule C line 27b")


def _schedule_c(b: Builder, income: dict[str, str], expenses: dict[str, str]) -> None:
    sch_c = b.ex.pages_of("sch_c")[0]
    business: dict[str, Any] = {"owner": "taxpayer", "expenses": {}}
    a_line = b.text(sch_c, 0, 400, 655, 675)
    c_line = b.text(sch_c, 0, 400, 630, 650)
    code = [re.sub(r"\D", "", " ".join(b.text(sch_c, 400, 620, 655, 675)))]
    business["name"] = c_line[0] if c_line else a_line[0]
    b.src("businesses[0].name", sch_c, "Schedule C line C, business name" if c_line else "Schedule C line A")
    if code:
        business["principal_business_code"] = re.sub(r"\D", "", code[0])
        b.src("businesses[0].principal_business_code", sch_c, "Schedule C line B")
    method = b.checked(sch_c, 572, 588)
    if method and method[0].startswith("Cash"):
        business["accounting_method"] = "cash"
        b.src("businesses[0].accounting_method", sch_c, "Schedule C line F: Cash")
    participation = b.checked(sch_c, 560, 575, 480)
    if participation and participation[0].startswith("Yes"):
        business["materially_participates"] = True
        b.src("businesses[0].materially_participates", sch_c, "Schedule C line G: Yes")
    for line, key in income.items():
        value = b.line("sch_c", line, path=f"businesses[0].{key}")
        if value is not None:
            business[key] = value
    for line, key in expenses.items():
        value = b.line("sch_c", line, path=f"businesses[0].expenses.{key}")
        if value is not None:
            business["expenses"][key] = value
    if a_line and c_line:
        b.unmodelled_fact(f"Schedule C line A (principal business or profession): {a_line[0]}; line E address", sch_c, METADATA)
    b.ret["businesses"] = [business]


def _digital_assets(b: Builder) -> None:
    answer = b.checked(2, 490, 520, 500)
    if answer:
        b.unmodelled_fact(f"Form 1040 digital asset question: {answer[0].split()[0]}", 2, METADATA)


def scenario_13(b: Builder) -> None:
    b.ret["filing_status"] = b.filing_status()
    h = b.header()
    b.ret["taxpayer"] = _taxpayer(b)
    sp = h["spouse"]
    b.ret["spouse"] = {"first_name": sp["first_name"], "last_name": sp["last_name"], "ssn": sp["ssn"]}
    b.src("spouse", 2, "Form 1040 page 1 spouse's name and SSN")
    p2 = b.ex.pages_of("f1040")[1]
    boxes_12d = b.checked(p2, 690, 715)
    if any(t.startswith("Was born before January 2, 1962") for t in boxes_12d):
        b.ret["spouse"]["dob"] = b.assume(
            "spouse.dob", "1962-01-01",
            "Form 1040 line 12d says only that the spouse was born before January 2, 1962; this is the latest such date "
            "(no rule the engine applies tells the dates apart)", alternatives=["1950-07-01"], page=p2)
    b.ambiguity(p2, "the taxpayer's date of birth is not stated (line 12d is checked for the spouse only); the engine "
                    "treats him as under 65")
    owners = {b.ret["taxpayer"]["ssn"]: "taxpayer", b.ret["spouse"]["ssn"]: "spouse"}
    for page in b.ex.pages_of("w2"):
        b.w2(page, owners)
    ordinary = b.line("f1040", "3b", path="dividends[0].ordinary")
    distributions = b.line("f1040", "7a", path="dividends[0].capital_gain_distributions")
    schedule_d_not_required = [t for t in b.checked(2, 75, 90) if t.startswith("Schedule D not required")]
    if ordinary is not None:
        dividend: dict[str, Any] = {"payer": "not named: no Form 1099-DIV is in the scenario", "ordinary": ordinary}
        if distributions is not None and schedule_d_not_required:
            dividend["capital_gain_distributions"] = b.assume(
                "dividends[0].capital_gain_distributions", distributions,
                "Form 1040 line 7a prints 500 with 'Schedule D not required' checked (line 7b), which the instructions "
                "allow only when the capital gains are capital gain distributions (Form 1099-DIV box 2a)", page=2)
        b.ret["dividends"] = [dividend]
        b.assume("dividends[0]", "one Form 1099-DIV",
                 "the scenario prints ordinary dividends (line 3b) and capital gain distributions (line 7a) on Form 1040 "
                 "but includes no Form 1099-DIV; they enter as one payer's amounts, with no qualified dividends (line 3a "
                 "is blank)", page=2)
    sch1a3 = b.ex.pages_of("sch_1a")[-1]
    vin = "".join(w.text for w in sorted((w for w in b.ex.page(sch1a3).words if w.fill and 620 < w.y < 640 and w.x0 < 400),
                                         key=lambda w: w.x0))
    interest = b.line("sch_1a", "28a", path="car_loans[0].interest_paid", alias="29",
                      note="the engine sums the vehicles' interest on line 29 (one vehicle here)")
    answers = b.checked(sch1a3, 585, 615)
    b.ret["car_loans"] = [{"vin": vin, "interest_paid": interest, "qualifies": all(a.startswith("Yes") for a in answers)}]
    b.src("car_loans[0].vin", sch1a3, "Schedule 1-A line 28a, column (i) vehicle identification number")
    b.src("car_loans[0].qualifies", sch1a3, f"Schedule 1-A Part IV questions answered {answers}")
    overtime_rows = [v for v in b.ex.lines if v.form == "sch_1a" and v.line == "16a" and not v.inline]
    if len(overtime_rows) == 1:
        b.aliases[("sch_1a", "16a")] = ("17", "the engine reports the overtime total (line 17), not the per-employer "
                                              "rows 16a-16e; one employer here")
    _digital_assets(b)
    deposit = b.checked(p2, 230, 245, 360)
    b.unmodelled_fact(f"Form 1040 lines 35b-35d direct deposit: routing and account numbers masked with X; type checked "
                      f"{deposit}", p2, METADATA)
    for form, why in (("sch_f", "Schedule F (farm income, Schedule 1 line 6)"),
                      ("f4136", "Form 4136 (credit for federal tax paid on fuels, Schedule 3 line 12)"),
                      ("f4136_sch_a", "Schedule A (Form 4136), two business activities"),
                      ("f1062", "Form 1062 (deferral of tax on a qualified farmland sale: Form 1040 line 24b, Schedule 3 "
                                "line 13z)"),
                      ("f1062_sch_a", "Schedule A (Form 1062), the qualified farmland sale")):
        pages = b.ex.pages_of(form)
        if pages:
            b.unmodelled_fact(f"{why}: pages {pages}", pages[0], "changes the return: the form is not modelled")
    b.unmodelled_fact("Schedule SE: self-employment tax on the Schedule F profit", b.ex.pages_of("sch_se")[0],
                      "changes the return: without Schedule F the engine has no self-employment income for Schedule SE")
    f1062 = b.ex.pages_of("f1062")
    if f1062:
        b.ambiguity(f1062[0], "Form 1062 line 6 (86,516, taxable income including the section 1062 gain) and line 7 "
                              "(56,516 of section 1062 gain) treat the farmland gain as part of taxable income, but no Form "
                              "1040 income line carries a gain of that size (line 7a is 500, line 8 is Schedule F's 10,000)")


def scenario_14(b: Builder) -> None:
    b.ret["filing_status"] = b.filing_status()
    b.ret["taxpayer"] = _taxpayer(b)
    b.ambiguity(1, "the taxpayer's date of birth is not stated (line 12d is not checked); the engine treats him as under 65")
    deps = b.dependents()
    eic = b.ex.pages_of("sch_eic")[0]
    p = b.ex.page(eic)
    year_digits = sorted((w for w in p.words if w.fill and 325 < w.y < 340), key=lambda w: w.x0)
    years = ["".join(w.text for w in year_digits[i:i + 4]) for i in range(0, len(year_digits), 4)]
    months = [w.text for w in sorted((w for w in p.words if w.fill and 60 < w.y < 80), key=lambda w: w.x0)]
    lived = b.checked(2, 405, 432, 140)
    out = []
    for i, d in enumerate(deps):
        year = int(years[i])
        out.append({"first_name": d["first_name"], "last_name": d["last_name"], "ssn": d["ssn"],
                    "relationship": d["relationship"], "months_in_home": int(months[i]),
                    "dob": b.assume(f"dependents[{i}].dob", f"{year}-07-01",
                                    f"Schedule EIC line 3 gives only the year of birth ({year}); no age test the engine "
                                    f"applies to a child born in {year} depends on the day", alternatives=[f"{year}-01-02",
                                                                                                         f"{year}-12-31"],
                                    page=eic)})
        b.src(f"dependents[{i}].months_in_home", eic, "Schedule EIC line 6")
    if len(lived) != 2 * len(deps):
        b.ambiguity(2, f"Form 1040 dependents column (5) check marks: {lived}")
    b.ret["dependents"] = out
    owners = {b.ret["taxpayer"]["ssn"]: "taxpayer"}
    for page in b.ex.pages_of("w2"):
        b.w2(page, owners)
    sch3a = b.ex.pages_of("sch_3a")[0]
    answers = b.checked(sch3a, 280, 460)
    if any(a.startswith("Yes. Go to line 8") for a in answers):
        b.ret["want_federal_public_benefit"] = True
        b.src("want_federal_public_benefit", sch3a, "Schedule 3-A line 7: Yes")
    if any(a.startswith("Yes. Enter -0-") for a in answers):
        b.ret["citizen_or_qualified_alien"] = True
        b.src("citizen_or_qualified_alien", sch3a, "Schedule 3-A line 8: Yes")
    _digital_assets(b)


SCENARIOS: dict[int, Callable[[Builder], None]] = {1: scenario_1, 2: scenario_2, 3: scenario_3, 4: scenario_4, 5: scenario_5,
                                                   6: scenario_6, 7: scenario_7, 12: scenario_12, 13: scenario_13,
                                                   14: scenario_14}


# ------------------------------------------------------------------------------------------------------------------ reviewed gap causes

# Why a line differs, worked by hand from the scenario's own figures and the engine's rules (2026 figures from Rev. Proc.
# 2025-32). Attached to the fixture as `gap_causes` (line-key patterns); the harness shows them and never compares them.
GAP_CAUSES: dict[int, list[tuple[str, str]]] = {
    12: [
        (r"f1040:(14|15)", "The scenario takes no qualified business income deduction (Form 1040 line 13b is blank). The engine "
                           "figures 1,610: 20% of Schedule C's 8,661 less the deductible part of self-employment tax (612) "
                           "(Form 8995); nothing in the scenario rules the deduction out."),
        (r"f1040:(16|18)", "Taxable income differs by the QBI deduction. On its own taxable income of 92,785 the scenario's tax "
                           "of 15,125 is the rate schedule's exact amount; the Tax Table, which the 2026 instructions prescribe "
                           "below $100,000 and the engine follows, gives 15,123 for that amount (midpoint 92,775). The engine's "
                           "14,771 is the Tax Table amount on its taxable income of 91,175 (midpoint 91,175)."),
        (r"f1040:(20|21|22|24a|24c|34|35a)|sch_3:(7|8)",
         "Form 3800 (the 10,000 general business credit on Schedule 3 line 6a, from Form 7207) is not modelled, so the engine "
         "has no credit on Schedule 3 lines 6a-8 and Form 1040 lines 20-21; the lines after them follow (the engine's return "
         "owes 1,551 where the scenario's is refunded 8,095)."),
    ],
    13: [
        (r"f1040:(16|18|22|24a|24c)",
         "Taxable income differs (Schedule F, below). Besides, the scenario's tax of 9,886 is the rate schedule's exact amount "
         "on all of its taxable income of 86,516: it neither uses the Tax Table nor taxes line 7a's 500 (capital gain "
         "distributions, 'Schedule D not required') at 0% through the Qualified Dividends and Capital Gain Tax Worksheet, as "
         "the engine does (8,645 = the Tax Table amount on 76,150 plus 0% on 500)."),
        (r"sch_1:(10|15|26)|f1040:(8|9|10|11a|11b|15|23)|sch_1a:(1|3|22|31|37)|sch_2:(4|15|21)|sch_se#taxpayer:.*",
         "Schedule F is not supported, so the farm profit of 10,000 (Schedule 1 line 6) and the self-employment tax on it "
         "(Schedule SE line 12 and Schedule 2 line 4: 268 in the scenario; its deductible half, 134, on Schedule 1 line 15) "
         "are missing from the engine's return: adjusted gross income 126,500 against 136,366, and the lines figured from it "
         "follow."),
        (r"sch_3:(14|15)|f1040:(31|32c|33|34|35a)",
         "Form 4136 (the 1,007 fuel tax credit, Schedule 3 line 12) and Form 1062 (5,288 entered as an other payment, "
         "Schedule 3 line 13z) are not modelled, so Schedule 3 lines 14-15 and Form 1040 lines 31-35a lack them; lines 34-35a "
         "also follow from the tax (the scenario besides counts the 1,007 again on line 32a, see the ambiguities)."),
    ],
    14: [
        (r"f1040:(16|18|19|21)|sch_8812:(13|14)",
         "The scenario figures the tax on 18,900 by the rate schedule (1,240 + 12% of 6,500 = 2,020); the Tax Table, which the "
         "2026 instructions prescribe below $100,000 and the engine follows, figures it at the row's midpoint, 18,925: "
         "1,240 + 12% of 6,525 = 2,023. The child tax credit is limited to the tax (Credit Limit Worksheet A), so lines 19 "
         "and 21 and Schedule 8812 lines 13-14 follow."),
        (r"f1040:28|sch_8812:(16a|17|27)",
         "Follows from the tax: the additional child tax credit is the child tax credit the tax cannot absorb (Schedule 8812 "
         "line 16a = 4,400 less line 14): 2,380 on the scenario's 2,020, 2,377 on the engine's 2,023."),
        (r"f1040:27a",
         "The scenario's earned income credit of 4,693 is what the TY2025 amounts give for two children (maximum 7,152, "
         "phase-out from 23,350 at 21.06%, at the EIC Table midpoint 35,025: 7,152 - 2,459 = 4,693); the engine applies the "
         "2026 amounts of Rev. Proc. 2025-32 §4.06 (maximum 7,316, phase-out from 23,890): 7,316 - 21.06% x 11,135 = 4,971."),
        (r"f1040:(32a|32c|33|34|35a)", "Follows from lines 27a and 28 (the refundable credits)."),
    ],
}


# ------------------------------------------------------------------------------------------------------------------ the scenario's own arithmetic

# lhs = sum(terms) ("-" subtracts); FLOORED takes zero for a negative result; CROSS: the first line repeats the second.
SUMS: list[tuple[str, str, list[str]]] = [
    ("f1040", "1z", ["1a", "1b", "1c", "1d", "1e", "1f", "1g", "1h"]), ("f1040", "9", ["1z", "2b", "3b", "4b", "5b", "6b", "7a", "8"]),
    ("f1040", "11a", ["9", "-10"]), ("f1040", "11b", ["11a"]), ("f1040", "14", ["12e", "12f", "13a", "13b"]),
    ("f1040", "18", ["16", "17"]), ("f1040", "21", ["19", "20"]), ("f1040", "24a", ["22", "23"]), ("f1040", "24c", ["24a", "24b"]),
    ("f1040", "25d", ["25a", "25b", "25c"]), ("f1040", "32a", ["27a", "28", "29", "30", "31"]), ("f1040", "32c", ["32a", "-32b"]),
    ("f1040", "33", ["25d", "26", "32c"]), ("sch_1", "10", ["1", "2a", "3", "4", "5", "6", "7", "9"]),
    ("sch_3", "8", ["1", "2", "3", "4", "5a", "5b", "7"]), ("sch_3", "15", ["9", "10", "11", "12", "14"]),
    ("sch_3a", "2", ["1a", "-1b"]), ("sch_3a", "5", ["3", "-4"]), ("sch_8812", "3", ["1", "2d"]), ("sch_8812", "8", ["5", "7"]),
    ("sch_8812", "12", ["8", "-11"]), ("sch_8812", "19", ["18a", "-2500"]), ("sch_se", "3", ["1a", "1b", "2"]),
    ("sch_se", "6", ["4c", "5b"]), ("sch_se", "12", ["10", "11"]), ("sch_c", "3", ["1", "-2"]), ("sch_c", "5", ["3", "-4"]),
    ("sch_c", "7", ["5", "6"]), ("sch_c", "29", ["7", "-28"]), ("sch_c", "31", ["29", "-30"]),
    ("sch_c", "28", ["8", "9", "10", "11", "12", "13", "14", "15", "16a", "16b", "17", "18", "19", "20a", "20b", "21", "22", "23",
                     "24a", "24b", "25", "26", "27a", "27b"]),
    ("sch_1a", "44", ["15", "27", "36", "43"]), ("sch_1a", "43", ["42a", "42b"]),
]
FLOORED: list[tuple[str, str, list[str]]] = [
    ("f1040", "15", ["11b", "-14"]), ("f1040", "22", ["18", "-21"]), ("f1040", "34", ["33", "-24c"]), ("f1040", "37", ["24c", "-33"]),
    ("sch_3a", "6", ["2", "-5"]),
]
CROSS: list[tuple[tuple[str, str], tuple[str, str]]] = [
    (("f1040", "8"), ("sch_1", "10")), (("f1040", "10"), ("sch_1", "26")), (("f1040", "13a"), ("sch_1a", "44")),
    (("f1040", "17"), ("sch_2", "3")), (("f1040", "19"), ("sch_8812", "14")), (("f1040", "20"), ("sch_3", "8")),
    (("f1040", "23"), ("sch_2", "21")), (("f1040", "28"), ("sch_8812", "27")), (("f1040", "31"), ("sch_3", "15")),
    (("f1040", "24b"), ("f1062", "15")), (("sch_3a", "1a"), ("f1040", "32a")), (("sch_3a", "1b"), ("f1040", "31")),
    (("sch_3a", "3"), ("f1040", "24a")), (("sch_8812", "1"), ("f1040", "11b")), (("sch_1a", "1"), ("f1040", "11b")),
    (("sch_1", "3"), ("sch_c", "31")), (("sch_1", "6"), ("sch_f", "34")), (("sch_1", "15"), ("sch_se", "13")),
    (("sch_2", "4"), ("sch_se", "12")),
]


def arithmetic(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], set[tuple[str, str]]]:
    """Checks the scenario's printed amounts against the forms' own arithmetic. Returns the failures (as ambiguities) and the
    lines that are contradicted: a line repeating another that prints a different amount, a line found only in failing
    sums, and any line derived from a contradicted one. A sum is checked only when its result line is printed; a blank
    line is zero on a completed form."""
    stated: dict[tuple[str, str], int] = {}
    pages: dict[tuple[str, str], int] = {}
    for e in entries:
        if not e.get("inline") and not e.get("column"):
            stated.setdefault((e["form"], e["line"]), e["value"])
            pages.setdefault((e["form"], e["line"]), e["page"])
    failures: list[dict[str, Any]] = []
    passing: set[tuple[str, str]] = set()
    failing: set[tuple[str, str]] = set()
    contradicted: set[tuple[str, str]] = set()
    derived: list[tuple[tuple[str, str], list[tuple[str, str]]]] = []      # identities that hold: result, operands

    def value(form: str, term: str) -> tuple[int, tuple[str, str] | None]:
        sign = -1 if term.startswith("-") else 1
        t = term.lstrip("-")
        if t.isdigit() and int(t) > 100:
            return sign * int(t), None
        return sign * stated.get((form, t), 0), (form, t) if (form, t) in stated else None

    for kind, items in (("sum", SUMS), ("floored", FLOORED)):
        for form, lhs, terms in items:
            if (form, lhs) not in stated:
                continue
            parts = [value(form, t) for t in terms]
            total = sum(v for v, _ in parts)
            if kind == "floored":
                total = max(0, total)
            involved = [(form, lhs)] + [k for _, k in parts if k]
            if total == stated[(form, lhs)]:
                passing.update(involved)
                derived.append(((form, lhs), [k for _, k in parts if k]))
            else:
                failing.update(involved)
                failures.append({"page": pages[(form, lhs)],
                                 "note": f"{form} line {lhs} prints {stated[(form, lhs)]}, but its formula "
                                         f"({' '.join(terms)}) gives {total} from the printed lines"})
    for (fa, la), (fb, lb) in CROSS:
        if (fa, la) in stated and (fb, lb) in stated:
            if stated[(fa, la)] == stated[(fb, lb)]:
                passing.update({(fa, la), (fb, lb)})
            else:
                contradicted.add((fa, la))
                failures.append({"page": pages[(fa, la)],
                                 "note": f"{fa} line {la} prints {stated[(fa, la)]}, but it repeats {fb} line {lb}, which "
                                         f"prints {stated[(fb, lb)]}"})
    contradicted |= failing - passing
    changed = True
    while changed:   # a line figured (by an identity that holds) from a contradicted line is contradicted too
        changed = False
        for lhs, operands in derived:
            if lhs not in contradicted and any(o in contradicted for o in operands):
                contradicted.add(lhs)
                changed = True
    return failures, contradicted


# ------------------------------------------------------------------------------------------------------------------ the fixture

def instance_of(ex: Extraction, form: str, page: int, owners: dict[str, str]) -> str | None:
    if form == "sch_c":
        firsts = [p for p in ex.pages_of("sch_c") if ex.forms[p - 1][1] == 1]
        before = [p for p in firsts if p <= page]
        return str(len(before)) if before else "1"
    if form == "sch_se":
        text = " ".join(t for _, _, t in fill_lines(ex.pages[page - 1]))
        for number, owner in owners.items():
            if number in text:
                return owner
        return "taxpayer"
    return None


def build(ex: Extraction) -> dict[str, Any]:
    from pypdf import __version__ as pypdf_version

    from agentledger.returns.ats import engine_key

    b = Builder(ex)
    SCENARIOS[ex.scenario](b)
    cover = b.cover
    title = next((t for t in cover if re.match(r"ATS Test\s+Scenario", t)), f"ATS Test Scenario {ex.scenario}")
    taxpayer = re.sub(r"^:?\s*|\s*Taxpayer$|^Taxpayer:\s*", "", next((t for t in cover if "Taxpayer" in t), "")).strip()
    taxpayer = re.sub(r"^:\s*", "", taxpayer)
    forms_listed = []
    for i, t in enumerate(cover):
        t2 = t.lstrip("•v ").strip()
        if re.match(r"(Form|Schedule|Binary Attachment)\b", t2) and not re.search(r"includes the following|Assume|is \$|line \d",
                                                                                     t2):
            forms_listed.append(t2)
            b.cover_used.add(i)
    for i, t in enumerate(cover):
        if re.match(r"(ATS Test|Taxpayer:|:\s*\w+ \w+ Taxpayer|SSN:|Test Scenario|Additional Information|Payment Information)", t) \
                or t.strip() in ("•", "v"):
            b.cover_used.add(i)
    for i, t in enumerate(cover):
        if i not in b.cover_used:
            b.unmodelled_fact(f"cover: {t}", 1, "not read by the fixture (review it)")
    owners: dict[str, str] = {}
    if b.ret:
        owners = {b.ret["taxpayer"]["ssn"]: "taxpayer"}
        if b.ret.get("spouse"):
            owners[b.ret["spouse"]["ssn"]] = "spouse"
    entries: list[dict[str, Any]] = []
    for v in ex.lines:
        e: dict[str, Any] = {"form": v.form, "line": v.line, "page": v.page, "value": v.value, "printed": v.raw}
        if v.column:
            e["column"] = v.column
        instance = instance_of(ex, v.form, v.page, owners)
        if instance:
            e["instance"] = instance
        if v.inline:
            e["inline"] = True
        alias = b.aliases.get((v.form, v.line))
        engine = engine_key(v.form, alias[0] if alias else v.line, v.column, instance)
        e["engine"] = engine
        if alias:
            e["note"] = f"printed on {v.form} line {v.line}; {alias[1]}"
        e["role"] = "info" if v.inline else "input" if (v.form, v.page, v.line, v.column) in b.consumed else "result"
        entries.append(e)
        ex.used.add(v.page)
    failures, contradicted = arithmetic(entries)
    for f in failures:
        b.ambiguity(f["page"], f["note"])
    for e in entries:
        if (e["form"], e["line"]) in contradicted and e["role"] == "result" and not e.get("column"):
            e["role"] = "ambiguous"
            e["note"] = "the scenario's own arithmetic contradicts this line (see ambiguities)"
    for issue in ex.issues:
        b.ambiguity(issue["page"], f"{issue['form']}: the amount {issue['text']} is typed on no numbered line "
                                   f"(at x {issue['x']:.0f}, y {issue['y']:.0f}); not read")
    pages = []
    for p, (form, part) in zip(ex.pages, ex.forms):
        read = "lines" if form in LINE_FORMS and (LINE_FORMS[form] is None or part in LINE_FORMS[form]) else \
            "cover" if form == "cover" else "boxes" if form in ("w2", "w2g", "f1099r") else "facts read by the scenario"
        pages.append({"page": p.number, "form": form, "part": part, "read_as": read, "used": p.number in ex.used,
                      "unmapped_glyphs": p.unmapped, "repaired_glyphs": p.repaired})
    return {
        "scenario": ex.scenario,
        "title": title,
        "taxpayer": taxpayer,
        "identities": "IRS test identities (names, SSNs, EINs and addresses invented by the IRS for ATS), not real people",
        "forms_listed": forms_listed,
        "source": {"file": ex.path.name, "sha256": ex.sha256, "page_count": len(ex.pages),
                   "pages_used": sorted(ex.used), "extracted_on": date.today().isoformat(), "extractor": f"pypdf {pypdf_version}",
                   "publisher": "Internal Revenue Service, TY2026 Form 1040 MeF ATS scenarios (Publication 1436), marked draft"},
        "return": b.ret,
        "provenance": b.provenance,
        "unmodelled": b.unmodelled,
        "assumptions": b.assumptions,
        "ambiguities": b.ambiguities,
        "pages": pages,
        "expected": entries,
        "gap_causes": [{"lines": pattern, "cause": cause} for pattern, cause in GAP_CAUSES.get(ex.scenario, [])],
    }


def scenario_files() -> dict[int, Path]:
    found: dict[int, Path] = {}
    for p in sorted(PDF_DIR.glob("*.pdf")):
        m = re.search(r"scenario-(\d+)", p.name)
        if m:
            found[int(m.group(1))] = p
    return found


def dump(scenario: int) -> None:
    files = scenario_files()
    if scenario not in files:
        raise SystemExit(f"scenario {scenario}: no PDF in {PDF_DIR}")
    ex = extract(files[scenario], scenario)
    print(f"# scenario {scenario}: {ex.path.name} sha256={ex.sha256}")
    for p, (form, part) in zip(ex.pages, ex.forms):
        print(f"\n== page {p.number}: {form} p{part}  (unmapped glyphs {p.unmapped}, repaired {p.repaired})")
        if form in ("cover",):
            for y, x, t in fill_lines(p):
                print(f"  {y:6.1f} {x:5.1f} {t}")
            continue
        if form in ("w2", "w2g", "f1099r", "unknown"):
            for box, ws in sorted(read_boxes(p).items()):
                print(f"  box {box:>3}: " + " | ".join(f"{w.text}@{w.x0:.0f},{w.y:.0f}" for w in ws))
            continue
        if form not in LINE_FORMS or (LINE_FORMS[form] is not None and part not in LINE_FORMS[form]):
            print("  (not read as lines) " + " | ".join(f"{t}@{x:.0f},{y:.0f}" for y, x, t in fill_lines(p))[:1800])
            continue
        for lv in [v for v in ex.lines if v.page == p.number]:
            col = f".{lv.column}" if lv.column else ""
            print(f"  line {lv.line}{col}{' (inline)' if lv.inline else ''} = {lv.value}   [{lv.raw} @ {lv.x0:.0f},{lv.y:.0f}]")
        for iss in [i for i in ex.issues if i["page"] == p.number]:
            print(f"  ?? {iss}")
        other = [w for w in p.words if w.fill and parse_amount(w.text) is None]
        if other:
            print("  text: " + " | ".join(f"{w.text}@{w.x0:.0f},{w.y:.0f}" for w in other)[:1500])


def _comparable(fixture: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(fixture))
    out.pop("recorded", None)
    out.get("source", {}).pop("extracted_on", None)
    return out


def write(path: Path, fixture: dict[str, Any]) -> None:
    path.write_text(json.dumps(fixture, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--dump", type=int, metavar="N", help="print what the reader sees in scenario N")
    ap.add_argument("--record", action="store_true", help="also run the engine and record each scenario's outcome")
    ap.add_argument("--check", action="store_true", help="compare a fresh extraction with the committed fixtures; write nothing")
    ap.add_argument("--table", action="store_true", help="print the per-scenario summary table for docs/ATS.md")
    ap.add_argument("--only", type=int, action="append", metavar="N", help="limit to scenario N (repeatable)")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    if args.dump is not None:
        dump(args.dump)
        return 0
    from agentledger.returns import ats

    if args.table:
        paths = ats.fixture_paths(OUT_DIR)
        fixtures = [ats.load(p) for p in paths]
        print(ats.table([ats.run(f) for f in fixtures], fixtures))
        return 0
    files = scenario_files()
    committed = {ats.load(p)["scenario"]: p for p in ats.fixture_paths(OUT_DIR)}
    if not files:
        print(f"no scenario PDFs in {PDF_DIR}: the committed fixtures are not re-extracted"
              + ("; their outcomes are re-recorded from the JSON" if args.record else ""))
        if not args.record:
            return 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    status = 0
    for number in sorted(set(files) | (set(committed) if args.record else set())):
        if args.only and number not in args.only:
            continue
        path = OUT_DIR / f"scenario-{number:02d}.json"
        old = ats.load(path) if path.exists() else None
        if number in files:
            fixture = build(extract(files[number], number))
            if old is not None and _comparable(old) == _comparable(fixture):
                fixture["source"]["extracted_on"] = old["source"]["extracted_on"]
        else:
            assert old is not None
            fixture = old          # no PDF here: the committed fixture's facts and expected lines stand as they are
        if args.check:
            same = old is not None and _comparable(old) == _comparable(fixture)
            print(f"scenario {number}: {'unchanged' if same else 'DIFFERS from the committed fixture'}")
            status |= 0 if same else 1
            continue
        if old is not None and "recorded" in old:
            fixture["recorded"] = old["recorded"]
        if args.record:
            report = ats.run(fixture)
            fixture["recorded"] = report.recorded()
            c = report.counts()
            print(f"scenario {number}: matched {c['matched']}, differ {c['differs']}, blocked {c['blocked']}, echo {c['echo']}, "
                  f"inputs not shown {c['input_unshown']}, ambiguous {c['ambiguous']}, echo mismatches {c['echo_mismatch']}")
        write(path, fixture)
        print(f"wrote {path.relative_to(REPO)}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
