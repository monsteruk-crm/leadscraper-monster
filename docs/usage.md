# Usage

## Local Development

### Prerequisites
- Python 3.11+
- Node.js (for the Vercel CLI)
- A PostgreSQL database with `DATABASE_URL` set

### Setup

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Install the Vercel CLI (once)
npm install -g vercel

# 4. Create a .env file in the repo root
#    DATABASE_URL=postgresql://...
#    OPENAI_API_KEY=sk-...
#    OPENAI_MODEL=gpt-4o-mini   # optional
#    LEADS_PAGE_SIZE=25         # optional
#    BRAVE_SEARCH_API_KEY=...   # optional, enables Brave connector
#    SEARCH_SOURCES=duckduckgo,brave,nominatim

# 5. Run locally — mirrors the production serverless environment
vercel dev -L
# Python API available at http://localhost:3000
# React dashboard available at http://localhost:3000/dashboard/
```

If you want to work on the React app directly, use the Vite dev server:

```bash
cd frontend
npm install
npm run dev
```

### Database initialisation

The app now initialises the schema automatically on startup and before scrape/search-history requests. If you still need to force a repair, use **Init DB** from the React dashboard settings surfaces, or:

```bash
curl -X POST http://localhost:3000/api/db/init
```

This creates all tables. It is safe to run multiple times (idempotent).

### Reset DB (wipe all data)

If the schema has changed (new columns, etc.), use **Reset DB** from the React dashboard settings surfaces, or:

```bash
curl -X POST http://localhost:3000/api/db/reset
```

**Warning:** this drops all tables and all data.

This project currently treats schema alignment as destructive-reset only. Update the DDL, then wipe and re-seed with `/api/db/reset` instead of attempting an in-place migration.

## Deployment to Vercel

```bash
# Production deploy
vercel --prod
```

After deploying, set `DATABASE_URL` and `OPENAI_API_KEY` in the Vercel project settings, then visit the deployed URL. The app will bootstrap its schema automatically; use **Init DB** only if you need to repair or re-seed a broken database manually.

## API Reference

### Health
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Returns DB connection status and stats |

The new React dashboard uses this endpoint as its first live API check.

### Database
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/db/init` | Create tables (idempotent) |
| `POST` | `/api/db/reset` | Drop all tables and recreate (destructive) |

### Config
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/config` | Get current runtime settings |
| `POST` | `/api/config` | Update runtime settings |

### Sessions
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/sessions` | List recent sessions |
| `POST` | `/api/sessions` | Create a new session |
| `GET` | `/api/sessions/{id}/history` | Get conversation turns |
| `PATCH` | `/api/sessions/{id}/rename` | Rename a session |

### Chat
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/chat` | Stream LeadBot reply as SSE tokens via the OpenAI Responses API; normal chat keeps recent `chat` turns, explicit web-search requests use isolated `search` turns, and referential search follow-ups can reuse only the latest search summary |

### Scrape
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/scrape` | Run scraping pipeline, stream LeadEvents as SSE, and semantically resume previous query depth by default |

### Leads
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/leads` | List leads (paginated, searchable) |
| `PATCH` | `/api/leads/{id}` | Update editable lead fields such as status and notes |
| `GET` | `/api/leads/export` | Download all leads as CSV |
| `PATCH` | `/api/leads/{id}/archive` | Archive or restore a lead |

`GET /api/leads` supports these query parameters:

- `page`
- `page_size`
- `search`
- `include_archived`
- `sort_by` (`company_name`, `contact_name`, `role`, `email`, `city`, `country`, `category`, `confidence`, `status`, `created_at`)
- `sort_dir` (`asc`, `desc`)
- `country`
- `status`
- `category`

Repeated `/api/scrape` runs with the same keyword now resume per source. DuckDuckGo and Brave continue from their last stored result page independently, while one-shot sources such as Nominatim are marked exhausted after their website-tag batch has been consumed.

Near-duplicate queries can now resume too: semantic lookup (`pg_trgm`) can map a new query to the closest historical query and continue from that stored depth when similarity exceeds the threshold.

`POST /api/scrape` request body also supports:

- `semantic_resume` (bool, default `true`) — enables semantic history matching for query resume depth.
- `similarity_threshold` (float, default `0.32`) — pg_trgm similarity threshold for semantic query matches.

New retrieval endpoints for search-depth history:

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/search-history` | Return semantic matches for a query (`query`, `limit`, `similarity_threshold`) |
| `POST` | `/api/search-history/resolve` | Resolve the single best resume cursor for a query |


### Stats & Runs
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/stats` | Counts for leads, sessions, runs, visited URLs |
| `GET` | `/api/runs` | List scrape run history |

## Slash Commands (Chat UI)

Plain text in the terminal is treated as chat. Commands must start with `/`.

| Command | Description |
|---|---|
| `/help` | Show all commands |
| `/health` | Check API and database health |
| `/stats` | Show current lead/session/run counts |
| `/sessions` | List sessions |
| `/new [name]` | Start a new session |
| `/load <id>` | Load a previous session by ID |
| `/name <name>` | Rename the current session |
| `/history [limit]` | Show recent turns summary |
| `/config` | Show current config |
| `/leads [search]` | Query the lead table from chat |
| `/export` | Open the CSV export |
| `/dbinit` | Initialise the database schema |
| `/dbreset` | Reset the database schema |
| `/clear` | Clear the visible terminal output |
| `/chat <message>` | Send an explicit chat message |
| `/search <query>` | Force a web-search chat request |
| `/scrape kw1, kw2` | Run scraper with given keywords |

`/new` creates a fresh backend session and clears the visible terminal/session-history view so the next exchange starts as a new conversation.

Session history now uses `mode` values of `chat`, `search`, and `scrape` so the UI can show search-derived turns without mixing them into normal chat memory.
