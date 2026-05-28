# Dashboard

The UI lives in `frontend/` as a React/Vite app served at `/dashboard/`.
The deployment mounts the frontend at `/dashboard/`, which keeps the Vite base path canonical during local `vercel dev` runs.

The new shell is MUI-based and intentionally feature-shaped:

- fixed top bar with API health status
- permanent navigation rail on desktop
- tighter summary cards and a wider terminal pane
- hero row now includes the Monster logo as a plain image beside the main dashboard shell copy, ending above the metrics divider
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
- separate city, country, and phone fields in the lead detail drawer
- created timestamp visible in the table and detail drawer
- live scrape feed that streams progress and newly found leads during a run
- live scrape feed paginated in groups of 5 so the panel stays compact
- lead pagination with server-backed sorting and page size
- leads table lives in a fixed-height scroll region so the action controls stay visible
- session drawer and settings dialog
- embedded `react-terminal` chat panel
- DB init/reset and export actions exposed in the React UI

The React screen is wired directly to the Python backend.

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
- Table shows company, contact, role, email, phone, city, country, category, confidence, status, and created time
- Column headers drive backend sorting via MUI `TableSortLabel`
- A live scrape feed beside the table shows progress messages and new leads immediately while `/api/scrape` is running
- The live scrape feed shows 5 items per page and resets to the newest page when new lead events arrive
- Clicking a row opens a lead detail drawer

### Terminal tab

The terminal is embedded in the dashboard using `react-terminal`.

- Plain text is sent to `POST /api/chat`
- Explicit commands must start with `/`

It includes commands for:

- `/help`
- `/health`
- `/stats`
- `/sessions`
- `/new [name]`
- `/load <session_id>`
- `/name <new name>`
- `/history [limit]`
- `/config`
- `/leads`
- `/export`
- `/dbinit`
- `/dbreset`
- `/clear`
- `/chat <message>`
- `/search <query>`
- `/scrape [kw1, kw2]`

`/new` now behaves as a hard conversation reset in the UI: it creates a fresh session, clears the terminal output, and empties the currently shown session-history panel before the next message.

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

- Add richer run history and session history browsing beyond the current summary panels
- Add more lead-sheet field editing once the API contract is stable for those fields
- Decide whether live scrape feed history should persist per session or remain an in-memory operator view only
- Add server-side filter option lists so country/status/category chips are not limited to values present on the current page
- Normalize stored country values consistently (for example `GB` -> `United Kingdom`) so filters and exports stay coherent
- Show per-source resume cursors in the live scrape feed so operators can see how deep repeated searches have progressed across DuckDuckGo and Brave

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

`GET /` redirects to `/dashboard/`.
