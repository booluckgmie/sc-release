"""Polite crawling: sitemap discovery, listing-page fetch, and catalogue merge."""
from __future__ import annotations

import json
import re
import time
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .parse import BASE, parse_page

SITEMAP = f"{BASE}/XTOPIA_sitemap.ashx"
# Sections that hold regulatory documents. /regulation/enforcement is deliberately excluded
# (large, different data shape); add it via --prefix if you want it.
DEFAULT_PREFIXES = [
    "/regulation/guidelines",
    "/regulation/practice-notes",
    "/regulation/guidance-notes-and-guiding-principles",
    "/regulation/technical-notes-and-circulars",
    "/regulation/regulatory-faqs",
    "/regulation/consultation-papers",
    "/regulation/licensing",
    "/regulation/corporate-governance",
    "/regulation/acts",
]
USER_AGENT = "sc-regdocs-collector/0.1 (+https://github.com/booluckgmie/sc-release)"


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    retry = Retry(total=3, backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET", "HEAD"))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


class Politeness:
    def __init__(self, session: requests.Session, delay: float = 1.0):
        self.session, self.delay, self._last = session, delay, 0.0
        self.robots = urllib.robotparser.RobotFileParser()
        try:
            r = session.get(f"{BASE}/robots.txt", timeout=20)
            self.robots.parse(r.text.splitlines())
        except requests.RequestException:
            self.robots.parse([])

    def allowed(self, url: str) -> bool:
        return self.robots.can_fetch(USER_AGENT, url)

    def wait(self) -> None:
        gap = self.delay - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()


def discover_pages(session, prefixes=None) -> list[str]:
    prefixes = prefixes or DEFAULT_PREFIXES
    xml = session.get(SITEMAP, timeout=30).text
    urls = re.findall(r"<loc>(.*?)</loc>", xml)
    keep = [u for u in urls if any(re.match(rf"^{re.escape(BASE)}{re.escape(p)}(/|$)", u) for p in prefixes)]
    return sorted(set(keep))


def crawl(prefixes=None, delay: float = 1.0, limit: int | None = None, log=print) -> list[dict]:
    session = make_session()
    polite = Politeness(session, delay)
    pages = discover_pages(session, prefixes)
    if limit:
        pages = pages[:limit]
    log(f"{len(pages)} pages to crawl")
    merged: dict[str, dict] = {}
    for i, url in enumerate(pages, 1):
        if not polite.allowed(url):
            log(f"  robots.txt disallows {url}")
            continue
        polite.wait()
        try:
            r = session.get(url, timeout=30)
            r.raise_for_status()
        except requests.RequestException as e:
            log(f"  [{i}/{len(pages)}] FAILED {url}: {e}")
            continue
        recs = parse_page(r.text, url)
        for rec in recs:
            ex = merged.get(rec["source_document_id"])
            if ex:  # same download ID listed on several pages
                ex["source_page_urls"] = sorted(set(ex["source_page_urls"] + rec["source_page_urls"]))
            else:
                merged[rec["source_document_id"]] = rec
        log(f"  [{i}/{len(pages)}] {len(recs):3d} docs  {url.replace(BASE, '')}")
    return list(merged.values())


def blank_collection() -> dict:
    return {"download_status": "pending", "file_sha256": None, "content_type": None, "bytes": None,
            "original_filename": None, "text_chars": None, "date_check": None,
            "first_seen_at": None, "last_listed_at": None, "last_checked_at": None,
            "still_listed": True, "previous_sha256": None, "content_changed_at": None}


def merge_catalogue(old: dict | None, fresh: list[dict]) -> dict:
    """Carry collection state forward by document ID; mark IDs that vanished from SC's pages."""
    ts = now()
    old_docs = {d["source_document_id"]: d for d in (old or {}).get("documents", [])}
    seen = set()
    docs = []
    for rec in fresh:
        i = rec["source_document_id"]
        seen.add(i)
        coll = {**blank_collection(), **(old_docs.get(i, {}).get("collection") or {})}
        coll["first_seen_at"] = coll["first_seen_at"] or ts
        coll["last_listed_at"], coll["still_listed"] = ts, True
        rec["collection"] = coll
        docs.append(rec)
    for i, d in old_docs.items():  # keep history of removed documents
        if i not in seen:
            d["collection"]["still_listed"] = False
            docs.append(d)
    docs.sort(key=lambda d: (d.get("sort_date") or "", d["source_document_id"]), reverse=True)
    return {"source": "sc_malaysia", "generated_at": ts, "count": len(docs), "documents": docs}


def load(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save(path: Path, cat: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cat, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
