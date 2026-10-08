"""Public regulatory documents: fetching, text extraction and source adapters."""

from __future__ import annotations

import hashlib
import html
import io
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterator
from urllib.parse import urljoin, urlparse

import httpx

UA = {"User-Agent": "AgentLedger-RegWatch/0.1 (+compliance monitoring)"}


@dataclass
class Document:
    key: str  # stable id, e.g. "fr:2026-20542"
    source: str
    title: str
    url: str
    published: str | None = None
    abstract: str = ""
    doc_type: str | None = None
    effective_on: str | None = None
    agencies: list[str] = field(default_factory=list)
    text: str | None = None
    text_url: str | None = None

    @property
    def domain(self) -> str:
        return (urlparse(self.url).hostname or "").removeprefix("www.")

    def fingerprint(self) -> str:
        return hashlib.sha256((self.key + (self.text or self.abstract or "")).encode()).hexdigest()[:16]


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|nav|header|footer|noscript)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>|</h[1-6]>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
    return re.sub(r"\n\s*\n+", "\n\n", raw).strip()


def pdf_to_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def fetch_text(url: str, timeout: float = 60) -> str:
    r = httpx.get(url, headers=UA, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        return pdf_to_text(r.content)
    if "html" in ctype or "<html" in r.text[:500].lower() or "<pre" in r.text[:2000].lower():
        return html_to_text(r.text)
    return r.text


def is_official(url: str, official_domains: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in official_domains)


# -- sources --------------------------------------------------------------------------------

def federal_register(agencies: list[str], lookback_days: int = 10, per_page: int = 100) -> Iterator[Document]:
    params: list[tuple[str, Any]] = [
        ("per_page", per_page), ("order", "newest"),
        ("conditions[publication_date][gte]", (date.today() - timedelta(days=lookback_days)).isoformat()),
    ]
    params += [("conditions[agencies][]", a) for a in agencies]
    for f in ("document_number", "title", "type", "abstract", "publication_date", "effective_on", "html_url",
              "raw_text_url", "agencies"):
        params.append(("fields[]", f))
    url = "https://www.federalregister.gov/api/v1/documents.json"
    while url:
        r = httpx.get(url, params=params if "?" not in url else None, headers=UA, timeout=60)
        r.raise_for_status()
        data = r.json()
        for d in data.get("results", []):
            yield Document(
                key=f"fr:{d['document_number']}", source="federal_register", title=d["title"], url=d["html_url"],
                published=d.get("publication_date"), abstract=d.get("abstract") or "", doc_type=d.get("type"),
                effective_on=d.get("effective_on"), agencies=[a.get("name", "") for a in d.get("agencies") or []],
                text_url=d.get("raw_text_url"),
            )
        url = data.get("next_page_url")
        params = []


IRS_ITEM = re.compile(
    r'<a href="(/newsroom/[^"]+)" rel="bookmark">\s*<span>(.*?)</span>.*?field__item">\s*(IR-\d{4}-\d+),\s*([^—<]+?)\s*—\s*(.*?)</div>',
    re.S,
)


def irs_newsroom(url: str = "https://www.irs.gov/newsroom/news-releases-for-current-month") -> Iterator[Document]:
    r = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60, follow_redirects=True)
    r.raise_for_status()
    for path, title, ir, when, abstract in IRS_ITEM.findall(r.text):
        yield Document(key=f"irs:{ir}", source="irs_newsroom", title=html.unescape(title).strip(),
                       url=urljoin("https://www.irs.gov", path), published=when.strip(),
                       abstract=html_to_text(abstract), doc_type="News release")


def web_page_links(url: str, keywords: list[str], source: str) -> Iterator[Document]:
    """Generic watcher: links on a page whose anchor text mentions a keyword."""
    r = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60, follow_redirects=True)
    r.raise_for_status()
    kw = [k.lower() for k in keywords]
    for href, label in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', r.text, re.S):
        text = html_to_text(label)
        if len(text) > 15 and any(k in text.lower() for k in kw):
            full = urljoin(url, href)
            yield Document(key=f"web:{hashlib.sha1(full.encode()).hexdigest()[:12]}", source=source, title=text[:200], url=full)
