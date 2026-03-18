# Dashboard

The transition UI now lives in `frontend/` as a React/Vite app served at `/dashboard/`.
The legacy Python dashboard still exists at `/` while the migration is in progress.

The new shell is MUI-based and intentionally feature-shaped:

- fixed top bar with API health status
- permanent navigation rail on desktop
- tighter summary cards and a wider terminal pane
- hero row now includes the Monster logo in its own panel beside the main dashboard shell copy
- terminal text reduced for denser operator output
- hero metric cards now use a label/value row with circular, metric-colored counters
- hero metric labels are larger and bolder
- pipeline overview tiles reuse the same metric card treatment in a compact size
- runtime stats are now shown as compact KPI cards with label/value on the same row
- leads table with search and detail drawer
- MUI data table with sticky headers, sortable columns, and rows-per-page controls
- filter chips for country, status, and category
- editable lead status from the table and detail drawer
- editable lead notes in the detail drawer
- separate city and country fields in the lead detail drawer
- created timestamp visible in the table and detail drawer
- live scrape feed that streams progress and newly found leads during a run
- lead pagination with server-backed sorting and page size
- session drawer and settings dialog
- embedded `react-terminal` chat panel
- DB init/reset and export actions exposed in the React UI

The React screen is now wired to the Python backend instead of mocked local data.

## React Shell

### Top bar

| Element | Action |
|---|---|
| Brand + status chip | Shows live API health |
| **Refresh** | Reloads health, stats, config, sessions, runs, and leads |
| **Sessions** | Opens the session drawer |
| **Settings** | Opens the settings dialog |

### Navigation rail

- Overview
- Leads
- Terminal
- Settings

The left navigation now syncs the active tab and scrolls the main content section into view.

### Overview tab

- Summary cards for leads, sessions, runs, and URLs from `GET /api/stats`
- Pipeline stage cards derived from `GET /api/config`, `GET /api/runs`, and the loaded lead set
- Recent session cards from `GET /api/sessions`
- Current session history preview from `GET /api/sessions/{id}/history`

### Leads tab

- Search box queries `GET /api/leads`
- Pagination uses the `page`/`page_size` response from `GET /api/leads`
- Default page size comes from the `LEADS_PAGE_SIZE` environment variable unless the user changes it in the table footer
- Country, status, and category chips apply backend filters and are remembered per session in the browser
- Archive toggle calls `PATCH /api/leads/{id}/archive`
- Status select calls `PATCH /api/leads/{id}`
- Export button opens `GET /api/leads/export`
- Table shows company, contact, role, email, city, country, category, confidence, status, and created time
- Column headers drive backend sorting via MUI `TableSortLabel`
- A live scrape feed beside the table shows progress messages and new leads immediately while `/api/scrape` is running
- Clicking a row opens a lead detail drawer

### Terminal tab

The terminal is embedded in the dashboard using `react-terminal`.

- Plain text is sent to `POST /api/chat`
- Explicit commands remain available for operator workflows

It includes commands for:

- `help`
- `health`
- `stats`
- `sessions`
- `new [name]`
- `load <session_id>`
- `name <new name>`
- `history [limit]`
- `config`
- `leads`
- `chat <message>`
- `scrape [kw1, kw2]`

### Settings tab

Settings now reads and writes the live config via:

- `GET /api/config`
- `POST /api/config`
- `POST /api/db/init`
- `POST /api/db/reset`
- `GET /api/leads/export`

### Session and lead drawers

- Session drawer lists live sessions, creates new sessions, renames the active session, and previews history
- Lead drawer shows the selected lead in more detail, can edit status and notes, and can archive or restore it

## TODO

- Port the remaining legacy Python-only flows so `/dashboard/` can replace `/`
- Add richer run history and session history browsing beyond the current summary panels
- Add more lead-sheet field editing once the API contract is stable for those fields
- Decide whether live scrape feed history should persist per session or remain an in-memory operator view only
- Add server-side filter option lists so country/status/category chips are not limited to values present on the current page
- Normalize stored country values consistently (for example `GB` -> `United Kingdom`) so filters and exports stay coherent
- Show the current DuckDuckGo result-page cursor in the live scrape feed so operators can see how deep a repeated search has progressed

## Live data boundary

The React shell now uses the existing FastAPI contract directly:

- `GET /api/health`
- `GET /api/stats`
- `GET /api/config`
- `POST /api/config`
- `GET /api/sessions`
- `POST /api/sessions`
- `PATCH /api/sessions/{id}/rename`
- `GET /api/sessions/{id}/history`
- `GET /api/leads`
- `GET /api/leads?page=&page_size=&search=&include_archived=&sort_by=&sort_dir=&country=&status=&category=`
- `PATCH /api/leads/{id}`
- `GET /api/leads/export`
- `PATCH /api/leads/{id}/archive`
- `GET /api/runs`
- `POST /api/chat`
- `POST /api/scrape`

The legacy Python UI still remains available at `/` while the React dashboard continues to absorb more of the product surface.
