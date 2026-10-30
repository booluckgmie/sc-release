"""Read-only JSON API over data/catalogue.json (built by the collector, committed to git).

Vercel serves this FastAPI `app` from api/index.py (all routes rewritten in vercel.json).
"""
from __future__ import annotations

import base64
import json
from functools import lru_cache
from pathlib import Path

from html import escape
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

CATALOGUE = Path(__file__).resolve().parent.parent / "data" / "catalogue.json"
DATE_FIELDS = {"sort_date", "revised", "updated", "issued", "effective", "applicable_until", "expired"}

app = FastAPI(
    title="SC Malaysia regulatory documents (unofficial)",
    description="Metadata index built from public pages on sc.com.my. Not an SC product; "
                "always confirm against the official source linked in each record.",
    version="0.1.0",
)


@lru_cache(maxsize=1)
def _cat() -> dict:
    if not CATALOGUE.exists():
        return {"generated_at": None, "documents": []}
    return json.loads(CATALOGUE.read_text(encoding="utf-8"))


def _public(d: dict) -> dict:
    return d


def _date_of(d: dict, field: str):
    return d.get("sort_date") if field == "sort_date" else d.get("dates", {}).get(field)


def _encode(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def _decode(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return max(0, int(base64.urlsafe_b64decode(cursor.encode()).decode()))
    except Exception:
        raise HTTPException(400, "invalid cursor")


@app.get("/health")
def health():
    c = _cat()
    return {"status": "ok", "catalogue_generated_at": c.get("generated_at"), "documents": len(c["documents"])}


@app.get("/v1/documents")
def list_documents(
    q: str | None = Query(None, description="case-insensitive title substring"),
    category: str | None = None,
    topic: str | None = None,
    document_type: str | None = Query(None, description="main | amendment_summary | faq"),
    family: str | None = Query(None, description="document_family_id"),
    year: int | None = Query(None, description="year of the record's primary date (revised > issued > effective)"),
    date_field: str = Query("sort_date", description=f"one of {sorted(DATE_FIELDS)}"),
    date_from: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    include_removed: bool = False,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
):
    if date_field not in DATE_FIELDS:
        raise HTTPException(400, f"date_field must be one of {sorted(DATE_FIELDS)}")
    rows = _cat()["documents"]
    ql = q.lower() if q else None

    def ok(d: dict) -> bool:
        if not include_removed and not d["collection"].get("still_listed", True):
            return False
        if ql and ql not in d["title"].lower():
            return False
        if category and d.get("category") != category:
            return False
        if topic and d.get("topic") != topic:
            return False
        if document_type and d["document_type"] != document_type:
            return False
        if family and d["document_family_id"] != family:
            return False
        if year and d.get("year") != year:
            return False
        if date_from or date_to:
            v = _date_of(d, date_field)
            if not v or (date_from and v < date_from) or (date_to and v > date_to):
                return False
        return True

    hits = [d for d in rows if ok(d)]
    start = _decode(cursor)
    page = hits[start:start + limit]
    nxt = _encode(start + limit) if start + limit < len(hits) else None
    return {"data": [_public(d) for d in page],
            "meta": {"total_matches": len(hits), "returned_count": len(page), "next_cursor": nxt,
                     "catalogue_generated_at": _cat().get("generated_at")}}


@app.get("/v1/documents/{document_id}")
def get_document(document_id: str):
    for d in _cat()["documents"]:
        if d["source_document_id"] == document_id.lower():
            related = [r["source_document_id"] for r in _cat()["documents"]
                       if r.get("parent_document_id") == d["source_document_id"]]
            return {"data": d, "related_document_ids": related}
    raise HTTPException(404, "document not found")


@app.get("/v1/families")
def families(category: str | None = None):
    fams: dict[str, dict] = {}
    for d in _cat()["documents"]:
        if d["document_type"] != "main" or (category and d.get("category") != category):
            continue
        f = fams.setdefault(d["document_family_id"], {"document_family_id": d["document_family_id"],
                                                      "title": d["title"], "category": d.get("category"),
                                                      "topic": d.get("topic"), "versions": 0,
                                                      "earliest": None, "latest": None})
        f["versions"] += 1
        sd = d.get("sort_date")
        if sd:
            f["earliest"] = min(filter(None, [f["earliest"], sd]))
            f["latest"] = max(filter(None, [f["latest"], sd]))
    return {"data": sorted(fams.values(), key=lambda f: (f["category"] or "", f["title"]))}


@app.get("/v1/stats")
def stats():
    docs = _cat()["documents"]
    status: dict[str, int] = {}
    checks: dict[str, int] = {}
    for d in docs:
        s = d["collection"]["download_status"].split(":")[0]
        status[s] = status.get(s, 0) + 1
        k = str(d["collection"].get("date_check"))
        checks[k] = checks.get(k, 0) + 1
    years = sorted({d["year"] for d in docs if d.get("year")})
    return {"documents": len(docs), "year_range": [years[0], years[-1]] if years else None,
            "download_status": status, "date_check": checks,
            "downloaded_and_hashed": sum(1 for d in docs if d["collection"].get("file_sha256")),
            "catalogue_generated_at": _cat().get("generated_at"),
            "note": "Coverage is limited to the section pages crawled; see README for scope."}


PAGE_SIZE = 25

PAGE_CSS = """
:root{--bg:#f6f7f9;--fg:#1b2430;--muted:#667085;--card:#fff;--line:#e3e7ee;--accent:#0b5cad}
@media(prefers-color-scheme:dark){:root{--bg:#10151c;--fg:#e8edf4;--muted:#9aa6b6;--card:#171e28;--line:#2a3441;--accent:#6aaeff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:24px 16px 48px}h1{margin:0 0 4px;font-size:1.6rem}
.sub{color:var(--muted);margin:0 0 16px;font-size:.9rem}
form{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;background:var(--card);
border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:16px}
label{display:flex;flex-direction:column;font-size:.75rem;color:var(--muted);gap:3px}
input,select,button{font:inherit;padding:7px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg)}
button{background:var(--accent);color:#fff;border-color:var(--accent);cursor:pointer;align-self:end}
.n{color:var(--muted);font-size:.85rem;margin:0 0 10px}
ul{list-style:none;margin:0;padding:0;display:grid;gap:10px}
li{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
li a{color:var(--accent);font-weight:600;text-decoration:none}li a:hover{text-decoration:underline}
.meta{color:var(--muted);font-size:.8rem;margin-top:4px;display:flex;flex-wrap:wrap;gap:4px 12px}
.pager{display:flex;justify-content:space-between;margin-top:16px}.pager a{color:var(--accent)}
footer{margin-top:28px;color:var(--muted);font-size:.8rem}footer a{color:var(--accent)}
"""


def _opts(values, selected, label):
    out = [f'<option value="">{label}</option>']
    for v in values:
        sel = " selected" if str(v) == str(selected or "") else ""
        out.append(f'<option value="{escape(str(v))}"{sel}>{escape(str(v))}</option>')
    return "".join(out)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home(
    q: str = "",
    year: str = "",
    category: str = "",
    document_type: str = "",
    date_from: str = "",
    date_to: str = "",
    sort: str = "newest",
    downloaded: str = "",
    offset: int = Query(0, ge=0),
):
    docs = [d for d in _cat()["documents"] if d["collection"].get("still_listed", True)]
    years = sorted({d["year"] for d in docs if d.get("year")}, reverse=True)
    cats = sorted({d["category"] for d in docs if d.get("category")})
    types = sorted({d["document_type"] for d in docs})
    ql = q.lower().strip()

    def ok(d):
        sd = d.get("sort_date")
        if ql and ql not in d["title"].lower() and ql not in (d.get("topic") or "").lower():
            return False
        if year and str(d.get("year")) != year:
            return False
        if category and d.get("category") != category:
            return False
        if document_type and d["document_type"] != document_type:
            return False
        if (date_from or date_to) and (not sd or (date_from and sd < date_from) or (date_to and sd > date_to)):
            return False
        if downloaded and not d["collection"].get("file_sha256"):
            return False
        return True

    hits = [d for d in docs if ok(d)]
    dated = sorted((d for d in hits if d.get("sort_date")), key=lambda d: d["sort_date"], reverse=sort != "oldest")
    hits = dated + [d for d in hits if not d.get("sort_date")]
    page = hits[offset:offset + PAGE_SIZE]

    base = {"q": q, "year": year, "category": category, "document_type": document_type,
            "date_from": date_from, "date_to": date_to, "sort": sort, "downloaded": downloaded}
    base = {k: v for k, v in base.items() if v}

    def link(off):
        return "/?" + urlencode({**base, "offset": off})

    items = []
    for d in page:
        pages = d.get("source_page_urls") or []
        src = f' · <a href="{escape(pages[0])}">source page</a>' if pages else ""
        items.append(
            f'<li><a href="{escape(d.get("download_url") or "#")}">{escape(d["title"])}</a>'
            f'<div class="meta"><span>{escape(d.get("sort_date") or "undated")}</span>'
            f'<span>{escape(d.get("category") or "")}</span><span>{escape(d["document_type"])}</span>'
            f'<span>{escape(d.get("date_text") or "")}{src}</span></div></li>')
    back, fwd = ("Newer", "Older") if sort != "oldest" else ("Older", "Newer")
    newer = f'<a href="{link(max(0, offset - PAGE_SIZE))}">&larr; {back}</a>' if offset else "<span></span>"
    older = f'<a href="{link(offset + PAGE_SIZE)}">{fwd} &rarr;</a>' if offset + PAGE_SIZE < len(hits) else "<span></span>"
    sel = lambda v, cur: " selected" if v == cur else ""
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>SC Regulatory Documents</title>
<style>{PAGE_CSS}</style></head><body><main>
<h1>SC Regulatory Documents</h1>
<p class="sub">Unofficial mirror of regulatory documents published by the Securities Commission Malaysia.
Always confirm against the source link on each document.</p>
<form method="get" action="/">
<label>Search<input name="q" value="{escape(q)}" placeholder="Title or topic"></label>
<label>Year<select name="year">{_opts(years, year, "All years")}</select></label>
<label>Category<select name="category">{_opts(cats, category, "All categories")}</select></label>
<label>Type<select name="document_type">{_opts(types, document_type, "All types")}</select></label>
<label>From<input type="date" name="date_from" value="{escape(date_from)}"></label>
<label>To<input type="date" name="date_to" value="{escape(date_to)}"></label>
<label>Sort<select name="sort"><option value="newest"{sel("newest", sort)}>Newest first</option>
<option value="oldest"{sel("oldest", sort)}>Oldest first</option></select></label>
<label>Downloaded<select name="downloaded"><option value="">Any</option>
<option value="1"{sel("1", downloaded)}>Verified only</option></select></label>
<button type="submit">Apply</button></form>
<p class="n">{len(hits)} document(s)</p>
<ul>{"".join(items) or "<li>No documents match.</li>"}</ul>
<div class="pager">{newer}{older}</div>
<footer>API: <a href="/docs">/docs</a> · <a href="/v1/documents?limit=10">/v1/documents</a> ·
<a href="/v1/families">/v1/families</a> · <a href="/v1/stats">/v1/stats</a> ·
catalogue generated {escape(str(_cat().get("generated_at")))}</footer></main></body></html>"""
    return html
