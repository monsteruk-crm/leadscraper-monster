# Dashboard

The UI is a dark-theme single-page app served at `/`. All JavaScript runs in the browser; no frontend framework is used.

## Header

| Element | Action |
|---|---|
| **LeadBot** logo | — |
| Session badge (e.g. `#3 Session 2026-03-17`) | Click to open the Sessions modal |
| **Leads N** button | Opens the Leads drawer |
| **Export CSV** | Downloads all leads as `leads.csv` |
| **Settings** | Opens the Settings modal |
| **Init DB** | Creates tables (safe to repeat) |
| **Reset DB** | Drops all tables and recreates schema — **destructive** |

## Sidebar

- **Stats** panel: live counts for Leads, URLs visited, Sessions, Runs.
- **Sessions** list: click any session to load its history.
- **+ New** / **Refresh** buttons at the bottom.

## Chat Area

The main panel. Messages are colour-coded:
- **User** (right, blue bubble) — your messages or commands
- **Assistant** (left, dark bubble) — LeadBot responses, streamed token by token
- **Scrape** (mono font, dark) — scrape progress and lead cards

Lead cards appear inline during a scrape, showing company name, confidence badge (green ≥ 0.7, yellow ≥ 0.4, red < 0.4), email, country, and category.

After a successful scrape, the Leads drawer opens automatically.

## Leads Drawer

Opens from the bottom, occupying 70% of the viewport height.

**Search box** — filters by company name, email, category, or country (300 ms debounce).

**Columns:**

| Column | Source |
|---|---|
| Company | `company_name` |
| First | `first_name` |
| Last | `last_name` |
| Title | `title` (job title) |
| Email | `email` |
| Phone | `phone` |
| Category | `category` |
| Country | `country` |
| Conf | `confidence` (0.00–1.00, colour-coded badge) |
| Status | `status` |
| — | Archive / Restore button |

**Footer:** total lead count, pagination (up to 10 pages shown), Export CSV button.

## Modals

### Settings
Configure scraping behaviour. Changes are saved to the DB via `POST /api/config` and take effect on the next scrape.

Fields: default keywords (one per line), max pages per keyword, target new leads, request delay, AI confidence threshold, enable AI enrichment toggle.

### Init DB
Calls `POST /api/db/init`. Safe to run at any time — only creates tables that don't exist yet.

### Reset DB
Calls `POST /api/db/reset`. Drops **all** tables (leads, sessions, chat turns, runs, visited URLs, settings) and recreates them. Use this when the schema has changed (e.g. new columns were added). All data is permanently deleted.

### Sessions
Lists all sessions with turn counts and last-updated date. Click a session to switch to it. Create a new session with an optional name.
