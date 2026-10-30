"""CLI:  python -m collector {discover|fetch|verify|stats} [options]"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .crawl import Politeness, crawl, load, make_session, merge_catalogue, save
from .download import fetch_one

CATALOGUE = Path("data/catalogue.json")
TEXT_DIR = Path("data/text")


def cmd_discover(a) -> int:
    fresh = crawl(a.prefix or None, delay=a.delay, limit=a.limit)
    if not fresh:
        print("No documents found; refusing to overwrite catalogue.", file=sys.stderr)
        return 1
    cat = merge_catalogue(load(CATALOGUE), fresh)
    save(CATALOGUE, cat)
    print(f"Saved {cat['count']} documents to {CATALOGUE}")
    return 0


def _due(doc, recheck_days):
    c = doc["collection"]
    if not c.get("still_listed", True):
        return False
    if c["download_status"] != "ok" and not c["download_status"].startswith("ok_"):
        return True  # pending or previously errored
    if recheck_days and c.get("last_checked_at"):
        last = datetime.strptime(c["last_checked_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - last > timedelta(days=recheck_days)
    return False


def cmd_fetch(a) -> int:
    cat = load(CATALOGUE)
    if not cat:
        print("Run `discover` first.", file=sys.stderr)
        return 1
    todo = [d for d in cat["documents"] if _due(d, a.recheck_days)]
    if a.year:
        todo = [d for d in todo if d.get("year") == a.year]
    if a.limit:
        todo = todo[: a.limit]
    print(f"{len(todo)} documents to fetch")
    session = make_session()
    polite = Politeness(session, a.delay)
    for n, d in enumerate(todo, 1):
        polite.wait()
        d["collection"] = fetch_one(session, d, TEXT_DIR if a.save_text else None)
        c = d["collection"]
        print(f"  [{n}/{len(todo)}] {d['sort_date']} {d['title'][:45]:45s} {c['download_status']:10s} "
              f"date_check={c['date_check']}")
        if n % 25 == 0:
            save(CATALOGUE, cat)  # checkpoint so a CI timeout does not lose progress
    save(CATALOGUE, cat)
    return 0


def cmd_verify(a) -> int:
    """Acceptance test: download earliest / median / latest main documents (+ extra years)."""
    cat = load(CATALOGUE)
    if not cat:
        print("Run `discover` first.", file=sys.stderr)
        return 1
    mains = sorted((d for d in cat["documents"] if d["document_type"] == "main" and d["sort_date"]),
                   key=lambda d: d["sort_date"])
    if not mains:
        print("No dated main documents found.", file=sys.stderr)
        return 1
    picks = {mains[0]["source_document_id"]: mains[0],
             mains[len(mains) // 2]["source_document_id"]: mains[len(mains) // 2],
             mains[-1]["source_document_id"]: mains[-1]}
    for y in a.year or []:
        cand = [d for d in mains if d["year"] == y]
        if cand:
            picks[cand[0]["source_document_id"]] = cand[0]
    session = make_session()
    polite = Politeness(session, a.delay)
    failures = 0
    print(f"{'date':10} {'status':12} {'type':5} {'bytes':>9}  {'date_check':10}  title")
    for d in picks.values():
        polite.wait()
        c = fetch_one(session, d, None)
        ok = c["download_status"] == "ok" and c["date_check"] in ("exact", "month_year")
        failures += 0 if ok else 1
        print(f"{d['sort_date']:10} {c['download_status'][:12]:12} {str(c['content_type'])[:5]:5} "
              f"{str(c['bytes']):>9}  {str(c['date_check']):10}  {d['title'][:50]} {'' if ok else '<-- CHECK'}")
        if a.write:
            d["collection"] = c
    if a.write:
        save(CATALOGUE, cat)
    print("PASS" if not failures else f"{failures} item(s) need review (date_check not_found/no_text is a "
          "warning for scanned or re-typeset PDFs, not necessarily an error)")
    return 0 if not failures else 2


def cmd_stats(_a) -> int:
    cat = load(CATALOGUE)
    if not cat:
        return 1
    docs = cat["documents"]
    years = sorted(y for y in {d["year"] for d in docs} if y)
    print(f"documents={len(docs)} years={years[0]}..{years[-1]}" if years else f"documents={len(docs)}")
    print("by type:", dict(Counter(d["document_type"] for d in docs)))
    print("by status:", dict(Counter(d["collection"]["download_status"].split(":")[0] for d in docs)))
    print("date_check:", dict(Counter(str(d["collection"]["date_check"]) for d in docs)))
    print("no sort_date:", sum(1 for d in docs if not d["sort_date"]))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="collector")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover", help="crawl listing pages -> data/catalogue.json")
    d.add_argument("--prefix", action="append", help="path prefix, e.g. /regulation/guidelines (repeatable)")
    d.add_argument("--delay", type=float, default=1.0)
    d.add_argument("--limit", type=int, help="crawl only the first N pages (testing)")
    d.set_defaults(fn=cmd_discover)
    f = sub.add_parser("fetch", help="download pending documents, hash, check dates")
    f.add_argument("--delay", type=float, default=1.0)
    f.add_argument("--limit", type=int)
    f.add_argument("--year", type=int)
    f.add_argument("--recheck-days", type=int, default=0, help="re-download docs last checked > N days ago")
    f.add_argument("--save-text", action="store_true", help="store full extracted text in data/text/")
    f.set_defaults(fn=cmd_fetch)
    v = sub.add_parser("verify", help="acceptance test on earliest/median/latest documents")
    v.add_argument("--year", type=int, action="append")
    v.add_argument("--delay", type=float, default=1.0)
    v.add_argument("--write", action="store_true", help="persist results into the catalogue")
    v.set_defaults(fn=cmd_verify)
    s = sub.add_parser("stats")
    s.set_defaults(fn=cmd_stats)
    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
