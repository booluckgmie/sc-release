"""Parse an SC Malaysia listing page into document records.

SC pages list documents as
  <li><a href=".../api/documentms/download.ashx?id=UUID">Title (pdf)</a>
      <i>(Revised: 30 September 2026)</i>
      <ul><li>...related docs...</li></ul></li>
Nested <li> items are treated as documents related to their parent.
"""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

SOURCE = "sc_malaysia"
BASE = "https://www.sc.com.my"
DL_RE = re.compile(r"download\.ashx\?id=([0-9a-fA-F-]{36})", re.I)

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DATE = r"(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})"
_LABELS = [
    ("revised", r"revised(?:\s+on)?"),
    ("updated", r"updated(?:\s+on)?"),
    ("issued", r"issued(?:\s+on)?"),
    ("effective", r"effective(?:\s+date)?(?:\s+of\s+revision)?"),
    ("applicable_until", r"applicable\s+until"),
    ("expired", r"expired(?:\s+on)?"),
]
_SORT_PRIORITY = ["revised", "updated", "issued", "effective", "unlabelled"]


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def to_iso(day: str, month: str, year: str) -> str | None:
    m = _MONTHS.get(month[:3].lower())
    if not m:
        return None
    try:
        return date(int(year), m, int(day)).isoformat()
    except ValueError:
        return None


def parse_dates(text: str) -> dict:
    """Return {label: iso} for labelled dates, 'unlabelled' for a bare date, and
    '<label>_additional' when a label appears more than once."""
    out: dict = {}
    covered: list[tuple[int, int]] = []
    for key, label in _LABELS:
        found = []
        for m in re.finditer(rf"(?i)\b{label}\s*:?\s*{_DATE}", text):
            iso = to_iso(m.group(1), m.group(2), m.group(3))
            if iso:
                found.append(iso)
                covered.append(m.span())
        if found:
            out[key] = found[0]
            if len(found) > 1:
                out[f"{key}_additional"] = found[1:]
    for m in re.finditer(_DATE, text):
        if any(s <= m.start() < e for s, e in covered):
            continue
        iso = to_iso(m.group(1), m.group(2), m.group(3))
        if iso and "unlabelled" not in out:
            out["unlabelled"] = iso
    return out


def sort_date(dates: dict) -> str | None:
    for k in _SORT_PRIORITY:
        if dates.get(k):
            return dates[k]
    return None


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _own_text(node: Tag) -> str:
    parts = []
    for c in node.children:
        if isinstance(c, Tag):
            if c.name in ("ul", "ol"):
                continue
            parts.append(c.get_text(" ", strip=True))
        else:
            parts.append(str(c).strip())
    return _clean(" ".join(p for p in parts if p))


_SECTION_SUFFIX = re.compile(
    r"\s+-\s+(Regulatory FAQs|Technical Notes and Circulars|Practice Notes|Guidelines|Licensing|"
    r"Consultation Papers|Acts|Corporate Governance|Guidance Notes and Guiding Principles)\s*$", re.I)
_GENERIC_LINK = re.compile(r"^(click\s+here|here|view|read|pdf|download\b.*|full\s+(text|document)\b.*)$", re.I)


def page_title(soup: BeautifulSoup) -> str | None:
    meta = soup.find("meta", property="og:title")
    t = _clean(meta["content"]) if meta and meta.get("content") else (_clean(soup.title.get_text()) if soup.title else "")
    t = re.sub(r"\s*\|\s*Securities Commission Malaysia\s*$", "", t)
    return _SECTION_SUFFIX.sub("", t) or None


def _in_nav(a: Tag) -> bool:
    for p in a.parents:
        if p.name in ("nav", "header", "footer"):
            return True
        if any(c.startswith("navi-") for c in (p.get("class") or [])):
            return True
    return False


def _strip_title(t: str) -> str:
    t = re.sub(r"(?i)\(\s*pdf\s*\)", "", t)
    t = re.sub(rf"(?i)\b(?:issued|dated)?\s*(?:on)?\s*{_DATE}", "", t)
    return _clean(re.sub(r"(?i)\s+(issued|dated|on)\s*$", "", _clean(t)))


def classify(title: str) -> str:
    t = title.lower()
    if re.search(r"summary of (key )?(amendment|updates|changes)|summary of .* updates", t):
        return "amendment_summary"
    if ("frequently" in t and "question" in t) or re.search(r"\bfaqs?\b", t):
        return "faq"
    return "main"


def parse_page(html: str, page_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    path = [p for p in urlparse(page_url).path.split("/") if p]
    category = path[1] if len(path) > 1 else (path[0] if path else "")
    topic = path[2] if len(path) > 2 else None

    ptitle = page_title(soup)
    containers: dict[int, dict] = {}
    order: list[int] = []
    for a in soup.find_all("a", href=DL_RE):
        if _in_nav(a):  # skip site navigation / footer menus
            continue
        doc_id = DL_RE.search(a["href"]).group(1).lower()
        cont = a.find_parent("li") or a.parent
        key = id(cont)
        if key not in containers:
            containers[key] = {"node": cont, "links": []}
            order.append(key)
        containers[key]["links"].append((doc_id, _clean(a.get_text(" ", strip=True))))

    records: dict[int, dict] = {}
    for key in order:
        node, links = containers[key]["node"], containers[key]["links"]
        labelled = [(i, t) for i, t in links if t]
        if not labelled:
            continue
        doc_id, raw_title = labelled[0]
        text = _own_text(node)
        title = _strip_title(raw_title)
        generic = bool(_GENERIC_LINK.match(title)) or not title
        if generic and ptitle:
            title = ptitle  # button-style link: the page title is the document title
        dates = parse_dates(text)
        if not dates and node.name != "li":  # button-style link: look at the surrounding content column
            col = node.find_parent(class_="col")
            if col is not None:
                dates = {k: v for k, v in parse_dates(_clean(col.get_text(" "))).items() if k != "unlabelled"}
        records[key] = {
            "source": SOURCE,
            "source_document_id": doc_id,
            "title": title,
            "page_title": ptitle,
            "document_type": classify(title),
            "category": category,
            "topic": topic,
            "dates": dates,
            "sort_date": sort_date(dates),
            "date_text": text,
            "source_page_urls": [page_url],
            "download_url": f"{BASE}/api/documentms/download.ashx?id={doc_id}",
            "parent_document_id": None,
            "variants": [
                {"label": t or None, "source_document_id": i,
                 "download_url": f"{BASE}/api/documentms/download.ashx?id={i}"}
                for i, t in links[1:] if i != doc_id
            ],
        }

    out = [records[k] for k in order if k in records]
    primary_ids = {r["source_document_id"] for r in out}
    for r in out:  # stray empty anchors in SC markup often point at another entry's primary link
        r["variants"] = [v for v in r["variants"] if v["source_document_id"] not in primary_ids]

    # SC's markup is not reliably nested, so relate summaries/FAQs to the nearest preceding
    # main document only when their dates agree (conservative; otherwise left unattached).
    last_main = None
    for rec in out:
        if rec["document_type"] == "main":
            last_main = rec
        elif last_main and rec["sort_date"] and rec["sort_date"] == last_main["sort_date"]:
            rec["parent_document_id"] = last_main["source_document_id"]
        rec_parent = next((r for r in out if r["source_document_id"] == rec["parent_document_id"]), None)
        if rec["document_type"] == "main":
            rec["document_family_id"] = slugify(rec["title"])
        else:
            anchor = slugify(rec_parent["title"]) if rec_parent else (slugify(topic) if topic else "unassigned")
            rec["document_family_id"] = f"{anchor}:{rec['document_type']}"
        rec["year"] = int(rec["sort_date"][:4]) if rec["sort_date"] else None
    return out
