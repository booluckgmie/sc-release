# sc-regdocs

Unofficial, read-only JSON index of regulatory documents published on the
[Securities Commission Malaysia](https://www.sc.com.my) website. SC publishes pages and PDF downloads,
not a catalogue API, so this repo builds one:

```
sitemap -> section pages -> parse download links + dates -> data/catalogue.json -> FastAPI on Vercel
                                   |
                                   +-> optional: download PDFs -> SHA-256, file type, text, date cross-check
```

Not affiliated with SC. Every record links back to the official page; confirm there before relying on it.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-collector.txt -r requirements.txt httpx

python -m pytest -q tests                      # parser + API tests (offline, fixtures from live pages)
python -m collector discover                   # crawl ~146 pages, ~3 min at 1 req/s -> data/catalogue.json
python -m collector verify --year 2009 --year 2019 --write   # acceptance test on real downloads
python -m collector fetch                      # download everything pending (slow; CI does this weekly)
python -m collector stats
uvicorn api.index:app --reload                 # local API at http://127.0.0.1:8000/docs
```

## Deploy (GitHub -> Vercel)

1. Create a GitHub repo, replace `YOUR-USER` in `collector/crawl.py` (the User-Agent), and push.
2. In Vercel: **Add New Project -> import the repo**. No build command needed; `vercel.json` routes
   everything to `api/index.py`, and only `requirements.txt` (FastAPI) is installed for the function.
3. `.github/workflows/collect.yml` runs weekly (and on demand): tests, `discover`, `fetch --recheck-days 30`,
   then commits `data/catalogue.json`. The push triggers a Vercel redeploy, so the API data is always a git
   commit you can diff.

## API

| Endpoint | Purpose |
|---|---|
| `GET /v1/documents` | filters: `q`, `category`, `topic`, `document_type`, `family`, `year`, `date_field`, `date_from`, `date_to`, `include_removed`; pagination: `limit`, `cursor` |
| `GET /v1/documents/{id}` | one record plus IDs of attached summaries/FAQs |
| `GET /v1/families` | one row per document family with version count and date range |
| `GET /v1/stats` | counts, year range, download/verification coverage |
| `GET /health` | liveness and catalogue timestamp |

`date_field` can be `sort_date`, `revised`, `updated`, `issued`, `effective`, `applicable_until` or `expired`.
The three dates are kept separate because SC distinguishes them: filtering on a single "year" would misclassify versions.

## Record shape

```json
{
  "source": "sc_malaysia",
  "source_document_id": "695a3662-705b-45ee-ac6d-4f4ec356bedc",
  "title": "Equity Guidelines",
  "document_type": "main",
  "document_family_id": "equity-guidelines",
  "category": "guidelines", "topic": "equity",
  "dates": {"revised": "2026-09-30"},
  "sort_date": "2026-09-30", "year": 2026,
  "date_text": "Equity Guidelines (pdf) (Revised: 30 September 2026)",
  "download_url": "https://www.sc.com.my/api/documentms/download.ashx?id=695a3662-705b-45ee-ac6d-4f4ec356bedc",
  "source_page_urls": ["https://www.sc.com.my/regulation/guidelines/equity"],
  "parent_document_id": null,
  "collection": {"download_status": "pending", "file_sha256": null, "date_check": null, "still_listed": true}
}
```

Unknown values stay `null`; the collector fills `collection.*` only after a download is validated
(`%PDF-` magic bytes, hash, text extraction, listing-date cross-check). A changed hash on re-download is
recorded in `previous_sha256` / `content_changed_at`, and IDs that disappear from SC's pages are kept with
`still_listed: false`.

## Verified on 30 Oct 2026 against the live site

- Crawl of 146 section pages produced 829 document records spanning 1993 to 2026 (`python -m collector stats`).
- Acceptance run downloaded 7 PDFs across 1993, 2009, 2013, 2019, 2021, 2024 and 2026: all were valid PDFs and
  6 of 7 matched the listing date in the document text. One 2009 document (Islamic fund management
  company set-up) did not contain its listing date and needs a manual look.
- The latest Equity Guidelines listing reads "Revised: 30 September 2026", ID `695a3662-...`.

## Known limits (read before trusting coverage)

- **HTML-only pages are not captured.** About 80 of the 146 crawled pages (many Technical Notes, FAQs,
  licensing pages) have no PDF link; their content is the page itself. Adding page-as-document records is the
  next obvious extension.
- **About 45% of records have no date** (381 of 829 at last run): mostly chapters, appendices and schedules
  of a larger guideline, which SC lists without a date. They are real documents but `year` is `null`.
- **Parent links are conservative.** A summary or FAQ is attached to a main document only when the nearest
  preceding main document has the same date. SC's markup is not reliably nested.
- Only 7 of 829 documents have been downloaded and hashed so far; run `fetch` (or the weekly workflow) for the rest.
- `/regulation/enforcement` is excluded by default. Pass `--prefix /regulation/enforcement` to include it.
- Collector etiquette: 1 request/second, honours `robots.txt` (currently allows everything), identifies itself.
  Check SC's terms of use before republishing document content; this repo stores metadata and hashes, and
  full text only if you opt in with `fetch --save-text`.
