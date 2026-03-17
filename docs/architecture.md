# Architecture

## Overview

LeadScraper Monster is a serverless FastAPI application. A single Python entry-point (`main.py`) handles both the SPA shell and all REST/SSE API routes. The scraping pipeline runs in the same process, streaming results back to the browser via Server-Sent Events (SSE).

```
Browser (SPA)
  │  POST /api/scrape          POST /api/chat
  │  (SSE stream)              (SSE stream)
  ▼
main.py  ── FastAPI / ASGI ──────────────────────────────────
  │
  ├── scraper/scraper.py       LeadScraper orchestrator
  │     ├── scraper/sources.py   DuckDuckGo search
  │     ├── scraper/parsers.py   HTML parsing, contact extraction
  │     ├── scraper/enricher.py  OpenAI enrichment
  │     └── scraper/models.py    Lead + ScrapeResult dataclasses
  │
  ├── db/postgres.py           asyncpg persistence layer
  └── config/config.py         Runtime config (env vars + DB settings)
```

## Modules

### `main.py`
- FastAPI app instance with CORS middleware
- Inline SPA HTML (dark-theme chat UI) served at `GET /`
- All REST endpoints under `/api/`
- SSE streaming for `/api/chat` and `/api/scrape`
- OpenAI chat assistant (LeadBot) with per-session history

### `scraper/models.py`
Dataclasses shared across all scraper modules.

**`Lead`** fields (matches Monster CRM schema):

| Field | Type | Notes |
|---|---|---|
| `company_name` | str | |
| `website` | str | |
| `country` | str | |
| `city` | str | |
| `first_name` | str | extracted by parser + AI |
| `last_name` | str | extracted by parser + AI |
| `contact_name` | str | `first_name + last_name`; kept for CSV compat |
| `title` | str | job title / role |
| `role` | str | legacy alias for `title` |
| `email` | str | |
| `phone` | str | |
| `source_url` | str | page the lead was found on |
| `category` | str | industry category |
| `size_signals` | str | textual clues about company size |
| `notes` | str | AI-generated notes |
| `confidence` | float | 0.0–1.0; AI-assigned quality score |
| `status` | str | New / Contacted / Qualified / etc. |
| `owner` | str | assigned CRM user |
| `last_touch` | str | date of last contact |
| `opt_out` | bool | GDPR opt-out flag |

`dedupe_key` — computed property: `email or website or company_name` (lowercased). Used to detect duplicates before inserting.

**`ScrapeResult`** — aggregates counts for one run: `leads_new`, `leads_duplicate`, `leads_discarded`, `pages_visited`.

### `scraper/sources.py`
Searches DuckDuckGo for each keyword and returns candidate URLs.
Controlled by `cfg.MAX_PAGES` and `cfg.REQUEST_DELAY_SECONDS`.

### `scraper/parsers.py`
Parses raw HTML into a `Lead`:
1. Scans `<a href="mailto:">` and `<a href="tel:">` links **before** noise removal.
2. Extracts company name, country, category from `<title>`, `<meta>`, and schema.org JSON-LD.
3. Tries to split `contact_name` into `first_name` / `last_name`.
4. Falls back to regex patterns for email / phone in body text.

### `scraper/enricher.py`
Calls OpenAI (`gpt-4o-mini` by default) with page text and the partially-filled `Lead`.
The system prompt asks the model to:
- Confirm or correct `company_name`, `country`, `category`
- Fill `first_name`, `last_name`, `title` from visible contact sections
- Set `confidence` (0.0–1.0)
- Generate brief `notes`
- Return structured JSON

### `db/postgres.py`
Async PostgreSQL layer via `asyncpg`. Uses a module-level connection pool (re-used across warm Vercel invocations).

**Tables:**

| Table | Purpose |
|---|---|
| `sessions` | Chat sessions |
| `chat_turns` | Conversation turns per session (role, content, mode) |
| `leads` | Scraped leads, unique on `dedupe_key` |
| `visited_urls` | Every URL ever fetched (prevents re-scraping) |
| `search_runs` | Log of every scrape run with stats |
| `settings` | Single-row config (id=1, singleton constraint) |

Key functions: `init_db()`, `reset_db()`, `insert_lead()`, `get_leads()`, `export_leads_csv()`, `get_stats()`.

### `config/config.py`
Reads environment variables and exposes mutable module-level globals.
Settings stored in the DB can override these at runtime via `apply_settings_to_config()`.
See [configuration.md](configuration.md) for the full variable list.

## Scraping Pipeline (per keyword)

```
search_duckduckgo(keyword)
    → [candidate URLs]
        → fetch_page(url)         [aiohttp, robots.txt check, delay]
            → parse_lead_info()   [BeautifulSoup4, JSON-LD, mailto/tel]
                → enrich_lead()   [OpenAI gpt-4o-mini, structured JSON]
                    → deduplicate [dedupe_key in existing_keys set]
                        → on_lead callback → db.insert_lead()
                            → yield LeadEvent("lead", {...})  [SSE]
```

## SSE Event Types

All events are `data: <JSON>\n\n` lines on the `/api/scrape` stream.

| `type` | Key fields | Notes |
|---|---|---|
| `progress` | `msg`, `phase` | Status updates (searching, fetching) |
| `lead` | `company_name`, `first_name`, `last_name`, `email`, `phone`, `website`, `category`, `confidence`, `country`, `city` | One event per new lead (city/country pre-filled by HTML parser if schema.org data present) |
| `done` | `leads_new`, `leads_duplicate`, `leads_discarded`, `pages_visited` | Final summary |
| `error` | `content` | Pipeline or DB error |
