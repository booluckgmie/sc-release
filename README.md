# sc-regdocs

Unofficial, read-only index of regulatory documents published on the
[Securities Commission Malaysia](https://www.sc.com.my) website. SC publishes pages and PDF downloads,
not a catalogue API, so this repo builds one and puts a browsable page in front of it.

**Live site:** https://sc-release-nqzz.vercel.app/ (API docs at `/docs`)

Not affiliated with SC. Every record links back to the official page; confirm there before relying on it.

## How it works

```
sitemap -> section pages -> parse download links + dates -> data/catalogue.json -> FastAPI on Vercel
                                   |                                                  |-> HTML list at /
                                   +-> optional: download PDFs -> SHA-256, file type, text, date cross-check
```

- **Collector** (`collector/`): crawls ~146 SC section pages at 1 request/second, parses document links and
  dates, and writes `data/catalogue.json` (829 documents, 1993 to 2026, at the last run).
- **API + UI** (`api/index.py`, entrypoint `app.py`): FastAPI app reading the committed catalogue.
  `/` is a server-rendered, filterable list; `/v1/*` is the JSON API.
- **Hosting**: Vercel project `sc-release-nqzz`, deployed from `main`. There is no `vercel.json`; Vercel detects
  the root `app.py` and keeps real request paths. (An earlier rewrite to `/api/index` made every route 404.)

## Web page (`/`)

Search (title or topic), year, category, document type, From/To date, newest/oldest first, and
"verified downloads only". 25 results per page with Newer/Older links. Each row links to the PDF and to the
SC source page. Search matches titles and topics only; PDF body text is not indexed.

## Update schedule

`.github/workflows/collect.yml` runs **daily at 02:00 UTC (10:00 Malaysia time)** and on demand
(GitHub: Actions -> collect -> Run workflow). Each run: tests, `discover` (re-crawl), `fetch --recheck-days 30`
(download new PDFs, re-check old ones monthly), `stats`, then commits `data/catalogue.json` only if it changed.
That push triggers a Vercel redeploy, so the live data is always a git commit you can diff.
If the push step fails, enable read and write access under Settings -> Actions -> General -> Workflow permissions.
`ci.yml` runs the tests on pushes and pull requests.

**Run history**
- Run #1 (manual, 30 Oct 2026) failed at the test step: `collect.yml` installed only `requirements-collector.txt`,
  so `tests/test_api.py` could not import `fastapi`. Fixed by installing `requirements.txt` and `httpx` too,
  matching `ci.yml`. Nothing was crawled or committed by that run; the catalogue is still the 30 Oct 2026 build.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-collector.txt -r requirements.txt httpx

python -m pytest -q tests                      # parser + API tests (offline, fixtures from live pages)
python -m collector discover                   # crawl ~146 pages, ~3 min at 1 req/s -> data/catalogue.json
python -m collector verify --year 2009 --year 2019 --write   # acceptance test on real downloads
python -m collector fetch                      # download everything pending (slow; the workflow does this daily)
python -m collector stats
uvicorn api.index:app --reload                 # local API at http://127.0.0.1:8000/docs
```

## Deploy (GitHub -> Vercel)

1. Push the repo to GitHub (the collector User-Agent already points at this repo).
2. In Vercel: **Add New Project -> import the repo**. No build command and no `vercel.json` are needed;
   `requirements.txt` (FastAPI) is installed for the function and `.vercelignore` keeps the collector, tests and
   the zip out of the bundle.
3. Every push to `main` redeploys, including the daily catalogue commit.

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
- Only 7 of 829 documents have been downloaded and hashed so far; run `fetch` (or the daily workflow) for the rest.
- `/regulation/enforcement` is excluded by default. Pass `--prefix /regulation/enforcement` to include it.
- Collector etiquette: 1 request/second, honours `robots.txt` (currently allows everything), identifies itself.
  Check SC's terms of use before republishing document content; this repo stores metadata and hashes, and
  full text only if you opt in with `fetch --save-text`.
