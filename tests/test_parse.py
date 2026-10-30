from pathlib import Path

from collector.parse import parse_dates, parse_page

FIX = Path(__file__).parent / "fixtures" / "equity_guidelines.html"
URL = "https://www.sc.com.my/regulation/guidelines/equity"


def _docs():
    return {d["source_document_id"]: d for d in parse_page(FIX.read_text(encoding="utf-8"), URL)}


def test_date_labels():
    d = parse_dates("Equity Guidelines (pdf) (Revised: 28 May 2026) (Effective Date of Revision: 3 June 2026)")
    assert d["revised"] == "2026-05-28" and d["effective"] == "2026-06-03"
    d = parse_dates("Equity Guidelines (pdf) ( Revised: 23 December 2020) (Applicable until: 31 December 2021)")
    assert d["revised"] == "2020-12-23" and d["applicable_until"] == "2021-12-31"
    assert parse_dates("Summary of 18 December 2013 Updates (pdf)") == {"unlabelled": "2013-12-18"}


def test_navigation_links_excluded():
    titles = {d["title"] for d in _docs().values()}
    assert "Organisation Structure" not in titles and "Derivatives" not in titles


def test_latest_equity_guidelines_and_related_summary():
    docs = _docs()
    main = docs["695a3662-705b-45ee-ac6d-4f4ec356bedc"]
    assert main["title"] == "Equity Guidelines" and main["document_type"] == "main"
    assert main["dates"]["revised"] == "2026-09-30" and main["year"] == 2026
    # stray empty anchor 6e6290d9 is the primary link of the 28 May 2026 revision, not a variant
    assert main["variants"] == []
    assert docs["6e6290d9-e4c5-4304-9fdf-d68c393562c7"]["dates"]["revised"] == "2026-05-28"
    summ = docs["f9221458-0de4-4bfd-90d7-db391ab0a9f5"]
    assert summ["document_type"] == "amendment_summary"
    assert summ["parent_document_id"] == main["source_document_id"]
    assert summ["dates"]["issued"] == "2026-09-30"


def test_historical_entries():
    docs = list(_docs().values())
    assert [d for d in docs if d["document_type"] == "faq" and d["dates"].get("issued") == "2009-05-08"]
    old = [d for d in docs if d["dates"].get("effective") == "2008-02-01"]
    assert old and all(d["dates"].get("expired") == "2009-08-02" for d in old)
    assert any(d["dates"].get("revised") == "2017-01-19" and d["dates"].get("applicable_until") == "2019-03-12"
               for d in docs)


def test_button_style_download_uses_page_title():
    html = (Path(__file__).parent / "fixtures" / "bumiputera_faq.html").read_text(encoding="utf-8")
    docs = parse_page(html, "https://www.sc.com.my/regulation/regulatory-faqs/bumiputera-equity-requirement-for-public-listed-companies")
    assert len(docs) == 1
    assert docs[0]["title"] == "Bumiputera Equity Requirement For Public Listed Companies"
    assert docs[0]["category"] == "regulatory-faqs"
