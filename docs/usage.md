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

# 5. Run locally — mirrors the production serverless environment
vercel dev
# App available at http://localhost:3000
```

### First-time DB initialisation

After starting for the first time, click **Init DB** in the header, or:

```bash
curl -X POST http://localhost:3000/api/db/init
```

This creates all tables. It is safe to run multiple times (idempotent).

### Reset DB (wipe all data)

If the schema has changed (new columns, etc.), click **Reset DB** in the header, or:

```bash
curl -X POST http://localhost:3000/api/db/reset
```

**Warning:** this drops all tables and all data.

## Deployment to Vercel

```bash
# Production deploy
vercel --prod
```

After deploying, set `DATABASE_URL` and `OPENAI_API_KEY` in the Vercel project settings, then visit the deployed URL and click **Init DB**.

## API Reference

### Health
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Returns DB connection status and stats |

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
| `POST` | `/api/chat` | Stream LeadBot reply as SSE tokens |

### Scrape
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/scrape` | Run scraping pipeline, stream LeadEvents as SSE |

### Leads
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/leads` | List leads (paginated, searchable) |
| `GET` | `/api/leads/export` | Download all leads as CSV |
| `PATCH` | `/api/leads/{id}/archive` | Archive or restore a lead |

### Stats & Runs
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/stats` | Counts for leads, sessions, runs, visited URLs |
| `GET` | `/api/runs` | List scrape run history |

## Slash Commands (Chat UI)

| Command | Description |
|---|---|
| `/scrape kw1, kw2` | Run scraper with given keywords |
| `/results [N]` | Show N most recent leads in chat (default 10) |
| `/config` | Open Settings modal |
| `/sessions` | Open Sessions modal |
| `/new [name]` | Start a new session |
| `/load <id>` | Load a previous session by ID |
| `/name <name>` | Rename the current session |
| `/recall <query>` | Ask LeadBot to search conversation history |
| `/history` | Show recent turns summary |
| `/clear` | Clear the chat view (DB history preserved) |
| `/help` | Show all commands |

Any other text is sent to LeadBot (OpenAI chat).
