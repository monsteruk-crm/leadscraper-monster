# Configuration

## Environment Variables

Set these in Vercel project settings (Production/Preview/Development) or in a local `.env` file for `vercel dev`.

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | Yes | — | PostgreSQL connection string (e.g. `postgresql://user:pass@host/db`) |
| `OPENAI_API_KEY` | Yes | — | OpenAI API key; without it AI enrichment is disabled |
| `OPENAI_MODEL` | No | `gpt-4o-mini` | OpenAI model used for enrichment and LeadBot chat |

**Important:** Never commit `.env` to git. Add `.env` to `.gitignore`.

## Runtime Settings (stored in DB)

These are persisted in the `settings` table (single row, `id=1`) and can be changed via the Settings modal in the UI or `POST /api/config`.

| Setting | Type | Default | Description |
|---|---|---|---|
| `keywords` | list[str] | `["sustainable packaging suppliers UK"]` | Default keyword list used when `/scrape` is run without arguments |
| `max_pages` | int | `3` | Maximum DuckDuckGo result pages to fetch per keyword |
| `target_new_leads` | int | `0` | Stop early when this many new leads are found; `0` = no limit |
| `request_delay_seconds` | float | `1.5` | Seconds to wait between HTTP requests (politeness) |
| `ai_enrichment_enabled` | bool | `true` | Toggle OpenAI enrichment on/off |
| `ai_confidence_threshold` | float | `0.0` | Discard leads with confidence below this value (0.0 = keep all) |

DB settings are loaded at the start of every scrape run via `db.apply_settings_to_config()`, overriding the module-level defaults in `config/config.py`.

## Adding a New Environment Variable

1. Add the variable to Vercel project settings.
2. Add a `os.environ.get(...)` line in `config/config.py`.
3. Update this file (`docs/configuration.md`).
4. If it changes architecture significantly, write an ADR in `docs/adr/`.
