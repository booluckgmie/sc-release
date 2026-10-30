"""Download a document, validate it, hash it, extract text, and cross-check dates."""
from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path

import requests

from .crawl import now

MAX_BYTES = 80 * 1024 * 1024
_MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august",
                "september", "october", "november", "december"]


def sniff(data: bytes) -> str:
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:2] == b"PK":
        return "zip_or_office"
    if data[:200].lstrip().lower().startswith((b"<!doctype html", b"<html")):
        return "html"
    return "unknown"


def extract_text(data: bytes, max_pages: int | None = None) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            return ""
    out = []
    for i, page in enumerate(reader.pages):
        if max_pages is not None and i >= max_pages:
            break
        try:
            out.append(page.extract_text() or "")
        except Exception:
            out.append("")
    return "\n".join(out)


def _norm(s: str) -> str:
    return re.sub(r"[\s\-–—,./]+", " ", s.lower()).strip()


def date_check(iso_dates: list[str], text: str) -> str:
    """Does any listing date appear in the document text? exact | month_year | not_found | no_text."""
    if not text.strip():
        return "no_text"
    t = _norm(text)
    for iso in iso_dates:
        y, m, d = iso.split("-")
        name = _MONTH_NAMES[int(m) - 1]
        if _norm(f"{int(d)} {name} {y}") in t or _norm(f"{int(d)} {name[:3]} {y}") in t:
            return "exact"
    for iso in iso_dates:
        y, m, _ = iso.split("-")
        if _norm(f"{_MONTH_NAMES[int(m) - 1]} {y}") in t:
            return "month_year"
    return "not_found"


def listing_dates(doc: dict) -> list[str]:
    d = doc.get("dates", {})
    vals = [d.get(k) for k in ("revised", "updated", "issued", "effective", "unlabelled")]
    vals += d.get("effective_additional", [])
    return [v for v in vals if v]


def fetch_one(session: requests.Session, doc: dict, text_dir: Path | None = None) -> dict:
    """Download one document and return an updated collection dict (does not mutate doc)."""
    coll = dict(doc["collection"])
    coll["last_checked_at"] = now()
    try:
        r = session.get(doc["download_url"], stream=True, timeout=(15, 90), allow_redirects=True)
        r.raise_for_status()
        buf = bytearray()
        for chunk in r.iter_content(65536):
            buf.extend(chunk)
            if len(buf) > MAX_BYTES:
                raise ValueError(f"exceeds {MAX_BYTES} bytes")
        data = bytes(buf)
    except (requests.RequestException, ValueError) as e:
        coll["download_status"] = f"error: {type(e).__name__}: {e}"[:200]
        return coll

    kind = sniff(data)
    sha = hashlib.sha256(data).hexdigest()
    if coll.get("file_sha256") and coll["file_sha256"] != sha:
        coll["previous_sha256"], coll["content_changed_at"] = coll["file_sha256"], now()
    cd = r.headers.get("content-disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd)
    coll.update(file_sha256=sha, bytes=len(data), content_type=kind,
                original_filename=m.group(1) if m else None)

    if kind != "pdf":
        coll.update(download_status="ok_non_pdf" if kind != "html" else "error: html_returned",
                    text_chars=None, date_check=None)
        return coll

    try:
        full = bool(text_dir)
        text = extract_text(data, None if full else 5)
        coll["text_chars"] = len(text) if full else None
        coll["date_check"] = date_check(listing_dates(doc), text[:60000])
        if full:
            text_dir.mkdir(parents=True, exist_ok=True)
            (text_dir / f"{doc['source_document_id']}.txt").write_text(text, encoding="utf-8")
        coll["download_status"] = "ok"
    except Exception as e:  # corrupt / unsupported PDF
        coll.update(download_status=f"error: pdf_parse: {type(e).__name__}"[:200], date_check=None)
    return coll
