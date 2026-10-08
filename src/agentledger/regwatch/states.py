"""State and legislative sources for RegWatch: all 50 states and DC, plus enacted federal and state law.

- Jurisdiction registry: config/jurisdictions.yaml, built from the Federation of Tax Administrators directory
  with reachability checks. It is a protected path, because its domains become trusted official sources.
- Agency news pages are discovered from each agency's home page (links whose text or path looks like news,
  announcements, bulletins or law changes) and verified before they are watched.
- Enacted legislation comes from Open States (all states; OPENSTATES_API_KEY) and GovInfo public laws
  (Congress; DATA_GOV_API_KEY). A source without a key reports itself unavailable instead of pretending.
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urljoin, urlparse

import httpx
import yaml

from .documents import Document, html_to_text

BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"}
NEWS_LINK = re.compile(r"news|press|announce|what'?s[- ]new|bulletin|tax[- ]?alert|law[- ]chang|legislat|guidance|notice|update", re.I)
TAX_KEYWORDS = ["tax", "rate", "bracket", "deduction", "exemption", "credit", "withholding", "inflation", "standard deduction",
                "legislation", "law change", "bill", "act", "conformity", "IRC", "filing", "extension", "relief", "pass-through",
                "PTET", "franchise", "nexus", "apportionment", "sales tax", "use tax"]
BOILERPLATE = re.compile(r"privacy|accessib|terms|disclaimer|subscri|unsubscri|login|sign[- ]?in|careers|jobs|contact|sitemap|"
                         r"social|scam|fraud[- ]alert|employment|procurement|vendor", re.I)
ARTICLE = re.compile(r"/(19|20)\d\d[-/]\d\d|/\d\d-\d\d-(19|20)\d\d|/article|/\d{4,}$", re.I)
TAX_BILL = re.compile(r"\btax|revenue|income|deduction|exemption|credit|levy|franchise|withholding|appropriat", re.I)


def load_jurisdictions(root: Path) -> list[dict[str, Any]]:
    return yaml.safe_load((root / "config" / "jurisdictions.yaml").read_text(encoding="utf-8"))["jurisdictions"]


def primary(j: dict[str, Any]) -> dict[str, Any] | None:
    return next((a for a in j["agencies"] if a["name"] == j.get("primary_agency")), None)


def state_domains(root: Path) -> list[str]:
    """Verified tax-agency domains (registrable host as listed) for every jurisdiction."""
    out = set()
    for j in load_jurisdictions(root):
        for a in j["agencies"]:
            if a.get("tax_agency") and a.get("domain"):
                out.add(a["domain"].lower().removeprefix("www."))
    return sorted(out)


def _same_site(url: str, base: str) -> bool:
    h, b = (urlparse(url).hostname or "").lower(), (urlparse(base).hostname or "").lower()
    return h == b or h.removeprefix("www.") == b.removeprefix("www.")


def _get(url: str, timeout: int = 30, tries: int = 3) -> httpx.Response:
    """GET with a browser user agent and backoff on transient 5xx and 429 (state sites are often rate-limited)."""
    import time

    last: httpx.Response | None = None
    for i in range(tries):
        last = httpx.get(url, headers=BROWSER_UA, timeout=timeout, follow_redirects=True)
        if last.status_code < 500 and last.status_code != 429:
            return last
        time.sleep(2 * (i + 1))
    return last


def _index_of(url: str) -> str:
    """An individual article URL rolls up to the listing page that contains it."""
    path = urlparse(url).path
    if ARTICLE.search(path):
        parent = path.rstrip("/").rsplit("/", 1)[0] + "/"
        return url.split(urlparse(url).path)[0] + parent
    return url


def discover_news_pages(agency_url: str, limit: int = 3) -> list[str]:
    """Candidate news/announcement pages linked from an agency home page, on the agency's own site."""
    r = _get(agency_url)
    r.raise_for_status()
    base = str(r.url)
    scored: dict[str, int] = {}
    for href, label in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', r.text, re.S):
        full = urljoin(base, href.strip())
        if not full.startswith("http") or not _same_site(full, base):
            continue
        text = html_to_text(label)
        if BOILERPLATE.search(text) or BOILERPLATE.search(urlparse(full).path):
            continue
        full = _index_of(full)
        score = (2 if NEWS_LINK.search(text) else 0) + (1 if NEWS_LINK.search(urlparse(full).path) else 0)
        if score and not full.lower().endswith((".pdf", ".jpg", ".png")):
            scored[full] = max(scored.get(full, 0), score + (1 if re.search(r"news|press", text + full, re.I) else 0))
    ranked = sorted(scored, key=lambda u: (-scored[u], len(u)))
    verified = []
    for u in ranked:
        try:
            if _get(u, timeout=20).status_code == 200:
                verified.append(u)
        except httpx.HTTPError:
            continue
        if len(verified) >= limit:
            break
    return verified


def page_items(url: str, source: str, keywords: list[str] | None = None) -> Iterator[Document]:
    """Tax-relevant links on a watched agency page."""
    r = _get(url, timeout=45)
    r.raise_for_status()
    kw = [k.lower() for k in (keywords or TAX_KEYWORDS)]
    seen: set[str] = set()
    for href, label in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', r.text, re.S):
        text = html_to_text(label)
        full = urljoin(str(r.url), href.strip())
        if len(text) < 15 or full in seen or not full.startswith("http") or not any(k in text.lower() for k in kw):
            continue
        seen.add(full)
        yield Document(key=f"state:{hashlib.sha1(full.encode()).hexdigest()[:12]}", source=source, title=text[:200], url=full,
                       doc_type="State guidance")


def openstates_enacted(jurisdiction_code: str, since: date, api_key: str | None = None) -> Iterator[Document]:
    """Tax bills that became law in a state since a date (Open States v3)."""
    key = api_key or os.environ.get("OPENSTATES_API_KEY")
    if not key:
        raise RuntimeError("OPENSTATES_API_KEY not set (free key at https://openstates.org/accounts/profile/)")
    page = 1
    while True:
        r = httpx.get("https://v3.openstates.org/bills", headers={"X-API-KEY": key}, timeout=45, params={
            "jurisdiction": jurisdiction_code.lower(), "action_since": since.isoformat(), "include": ["actions", "sources"],
            "per_page": 20, "page": page, "sort": "latest_action_desc"})
        r.raise_for_status()
        d = r.json()
        for b in d.get("results", []):
            actions = b.get("actions") or []
            enacted = [a for a in actions if set(a.get("classification") or []) & {"became-law", "executive-signature"}]
            if not enacted or not TAX_BILL.search(b.get("title", "")):
                continue
            url = next((s["url"] for s in b.get("sources", []) if s.get("url")), b.get("openstates_url", ""))
            yield Document(key=f"openstates:{b['id']}", source=f"openstates-{jurisdiction_code.lower()}",
                           title=f"{jurisdiction_code} {b.get('identifier')}: {b.get('title', '')}"[:300], url=url,
                           published=enacted[-1].get("date"), doc_type="Enacted state law")
        if page >= (d.get("pagination") or {}).get("max_page", 1):
            break
        page += 1


def govinfo_public_laws(since: date, api_key: str | None = None) -> Iterator[Document]:
    """Public laws published since a date (GovInfo PLAW collection); tax-related titles only."""
    key = api_key or os.environ.get("DATA_GOV_API_KEY")
    if not key:
        raise RuntimeError("DATA_GOV_API_KEY not set (free key at https://api.data.gov/signup/)")
    r = httpx.get(f"https://api.govinfo.gov/collections/PLAW/{since.isoformat()}T00:00:00Z", timeout=45,
                  params={"api_key": key, "pageSize": 100, "offsetMark": "*"})
    r.raise_for_status()
    for p in r.json().get("packages", []):
        if TAX_BILL.search(p.get("title", "")):
            yield Document(key=f"plaw:{p['packageId']}", source="govinfo-plaw", title=p.get("title", "")[:300],
                           url=f"https://www.govinfo.gov/app/details/{p['packageId']}", published=p.get("dateIssued"),
                           doc_type="Enacted federal law")


def lookback(days: int) -> date:
    return date.today() - timedelta(days=days)
