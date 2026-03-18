# Architecture

## Overview

LeadScraper Monster is being split into two surfaces:

- `frontend/` contains the new React dashboard scaffold
- `main.py` exposes the Python API and still serves the legacy dashboard during the transition

The scraping pipeline runs in the Python process, streaming results back to the browser via Server-Sent Events (SSE).

```
Browser (React SPA)         Browser (legacy Python UI)
  │  GET /dashboard/         │  GET /
  │  GET /api/health         │  POST /api/scrape
  ▼                          ▼
frontend/                   main.py  ── FastAPI / ASGI ─────────
  │                         │
  └── React/Vite shell      ├── scraper/scraper.py       LeadScraper orchestrator
                            │     ├── scraper/sources.py   DuckDuckGo search
                            │     ├── scraper/parsers.py   HTML parsing, contact extraction
                            │     ├── scraper/enricher.py  OpenAI enrichment
                            │     └── scraper/models.py    Lead + ScrapeResult dataclasses
                            │
                            ├── db/postgres.py           asyncpg persistence layer
                            └── config/config.py         Runtime config (env vars + DB settings)
```

## Modules

### `frontend/`
- React/Vite app that renders the transition dashboard
- MUI provides the shell, cards, tables, drawers, and dialogs
- `react-terminal` provides the embedded operator terminal
- Calls the existing FastAPI REST and SSE endpoints directly for health, stats, config, sessions, leads, chat, and scrape workflows
- Uses `/dashboard/` as its Vercel route prefix

### `main.py`
- FastAPI app instance with CORS middleware
- Legacy inline SPA HTML (dark-theme chat UI) still served at `GET /`
- All REST endpoints under `/api/`
- SSE streaming for `/api/chat` and `/api/scrape`
- OpenAI chat assistant (LeadBot) with per-session history
- `_resolve_session(session_id)` — validates the session exists before use; falls back to the latest session or creates a new one (prevents FK violations after Reset DB)

### `scraper/models.py`
Dataclasses shared across all scraper modules.

**`Lead`** fields (scraper working model):

| Field | Type | Notes |
|---|---|---|
| `company_name` | str | |
| `website` | str | |
| `country` | str | |
| `city` | str | |
| `first_name` | str | extracted by parser + AI; not persisted as a standalone DB column |
| `last_name` | str | extracted by parser + AI; not persisted as a standalone DB column |
| `contact_name` | str | persisted/exported canonical contact column |
| `title` | str | scraper/enricher working field collapsed into `role` at persistence time |
| `role` | str | persisted/exported canonical role column |
| `email` | str | |
| `phone` | str | |
| `source_url` | str | page the lead was found on |
| `category` | str | industry category |
| `size_signals` | str | textual clues about company size; exported as `size/signals` |
| `notes` | str | AI-generated notes |
| `confidence` | float | 0.0–1.0; AI-assigned quality score |
| `status` | str | New / Contacted / Qualified / etc. |
| `owner` | str | assigned CRM user |
| `last_touch` | str | date of last contact |
| `opt_out` | bool | GDPR opt-out flag |

`dedupe_key` — computed property: `email or website or company_name` (lowercased). Used to detect duplicates before inserting.

### Lead sheet alignment

`docs/lead_sheet_schema.csv` is the canonical external sheet shape.

- The DB/export path now aligns to `company_name, website, country, city, contact_name, role, email, source_url, category, size/signals, notes, confidence, status, owner, last_touch, opt_out`
- Internal operational columns still exist where needed (`id`, `phone`, `dedupe_key`, `created_at`, `session_id`, `archived`)
- `created_at` is now exposed in the React leads table and is the default sort key for `GET /api/leads`
- The app intentionally avoids a migration path here; schema changes are expected to be applied with a destructive reset via `/api/db/reset`

**`ScrapeResult`** — aggregates counts for one run: `leads_new`, `leads_duplicate`, `leads_discarded`, `pages_visited`.

### `scraper/sources.py`
Searches DuckDuckGo for each keyword and returns candidate URLs.
Controlled by `cfg.MAX_PAGES` and `cfg.REQUEST_DELAY_SECONDS`.

### `scraper/parsers.py`
Parses raw HTML into a `Lead`:
1. Scans `<a href="mailto:">` and `<a href="tel:">` links **before** noise removal.
2. Extracts `city` and `country` from schema.org JSON-LD (`addressLocality`/`addressCountry`), microdata (`itemprop`), and `geo.placename` meta tags.
3. Normalizes common country aliases (`GB`, `UK`, `US`, etc.), splits combined place strings when a page exposes city/country in one meta field, and drops obvious street-address strings from the `city` field.
4. Extracts company name from `<title>`, `<meta og:site_name>`, and schema.org JSON-LD.
5. Tries to split `contact_name` into `first_name` / `last_name` via schema.org `Person` markup.
6. Falls back to regex patterns for email / phone in body text.

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

**Pool behaviour:**
- `statement_cache_size=0` — disables asyncpg's prepared-statement cache, preventing stale plan errors after `reset_db()` or schema changes.
- `get_pool()` — checks `pool._closed` and recreates the pool if needed (handles hot-reload and post-reset states).
- `_close_pool()` — called by `reset_db()` after DDL so the next request gets a clean pool.
- `get_conn()` — on `InterfaceError`/`OSError` it closes the pool (so the next request reconnects) and re-raises; no illegal double-yield retry.

**Settings seed:** `init_db()` inserts seed settings with `ON CONFLICT DO NOTHING` (safe to re-run). `reset_db()` always upserts the seed so defaults are restored after a wipe.

**Tables:**

| Table | Purpose |
|---|---|
| `sessions` | Chat sessions |
| `chat_turns` | Conversation turns per session (role, content, mode) |
| `leads` | Scraped leads, unique on `dedupe_key` |
| `visited_urls` | Every URL ever fetched (prevents re-scraping) |
| `search_runs` | Log of every scrape run with stats |
| `search_progress` | Per-keyword DuckDuckGo result-page cursor for resumed searches |
| `settings` | Single-row config (id=1, singleton constraint) |

Key functions: `init_db()`, `reset_db()`, `_close_pool()`, `insert_lead()`, `get_leads()`, `export_leads_csv()`, `get_stats()`.

`get_leads()` now supports server-side pagination, backend filters (`country`, `status`, `category`), and a whitelisted sort contract (`company_name`, `contact_name`, `role`, `email`, `city`, `country`, `category`, `confidence`, `status`, `created_at`). Search also includes `city`.

### `config/config.py`
Reads environment variables and exposes mutable module-level globals.
Settings stored in the DB can override these at runtime via `apply_settings_to_config()`.
See [configuration.md](configuration.md) for the full variable list.

## Scraping Pipeline (per keyword)

```
search_duckduckgo(keyword)
    → [candidate URLs, next DDG page cursor]
        → fetch_page(url)         [aiohttp, robots.txt check, delay]
            → parse_lead_info()   [BeautifulSoup4, JSON-LD, mailto/tel]
                → enrich_lead()   [OpenAI gpt-4o-mini, structured JSON]
                    → deduplicate [dedupe_key in existing_keys set]
                        → on_lead callback → db.insert_lead()
                            → yield LeadEvent("lead", {...})  [SSE]

The scraper stores the next DuckDuckGo result page per keyword in `search_progress`, so rerunning the same search continues deeper through results instead of starting at page 1 every time.
```

## SSE Event Types

All events are `data: <JSON>\n\n` lines on the `/api/scrape` stream.

| `type` | Key fields | Notes |
|---|---|---|
| `progress` | `msg`, `phase` | Status updates (searching, fetching) |
| `lead` | `company_name`, `first_name`, `last_name`, `email`, `phone`, `website`, `category`, `confidence`, `country`, `city` | One event per new lead (city/country pre-filled by HTML parser if schema.org data present) |
| `done` | `leads_new`, `leads_duplicate`, `leads_discarded`, `pages_visited` | Final summary |
| `error` | `content` | Pipeline or DB error |
| `warning` | `msg` | Non-fatal issue (e.g. DB save failed for one lead) — shown in yellow in UI |
