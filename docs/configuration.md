# Configuration

## Environment Variables

Set these in Vercel project settings (Production/Preview/Development) or in a local `.env` file for `vercel dev`.

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | Yes | — | PostgreSQL connection string (e.g. `postgresql://user:pass@host/db`) |
| `OPENAI_API_KEY` | Yes | — | OpenAI API key; without it AI enrichment is disabled |
| `OPENAI_MODEL` | No | `gpt-4o-mini` | OpenAI model used for enrichment and LeadBot chat |
| `LEADS_PAGE_SIZE` | No | `25` | Default rows per page for `GET /api/leads` and the React MUI table |
| `SEARCH_SOURCES` | No | `bing,duckduckgo,brave,nominatim` | Ordered source list; unsupported names are ignored |
| `BRAVE_SEARCH_API_KEY` | No | `""` | Enables the Brave Search API connector when set |
| `BRAVE_RESULTS_PER_PAGE` | No | `20` | Brave web results fetched per page (max `20`) |
| `ENABLE_NOMINATIM` | No | `true` | Enables the Nominatim business-directory connector |
| `NOMINATIM_BASE_URL` | No | `https://nominatim.openstreetmap.org/search` | Override for self-hosted Nominatim |
| `NOMINATIM_LIMIT` | No | `10` | Max Nominatim entries to inspect per query |
| `REQUEST_DELAY_SECONDS` | No | `1.5` | Default polite delay between outbound requests |
| `RESPECT_ROBOTS_TXT` | No | `true` | Skip site fetches disallowed by `robots.txt` |

**Important:** Never commit `.env` to git. Add `.env` to `.gitignore`.

## Runtime Settings (stored in DB)

These are persisted in the `settings` table (single row, `id=1`) and can be changed via the Settings modal in the UI or `POST /api/config`.

| Setting | Type | Default | Description |
|---|---|---|---|
| `keywords` | list[str] | 4 seed keywords (sustainable/eco packaging UK) | Default keyword list used when `/scrape` is run without arguments |
| `max_pages` | int | `3` | Maximum paged result batches to fetch per keyword, per paged source |
| `target_new_leads` | int | `0` | Stop early when this many new leads are found; `0` = no limit |
| `request_delay_seconds` | float | `1.5` | Seconds to wait between HTTP requests (politeness) |
| `ai_enrichment_enabled` | bool | `true` | Toggle OpenAI enrichment on/off |
| `ai_confidence_threshold` | float | `0.3` | Discard leads with confidence below this value (0.0 = keep all) |
| `leads_default_country` | str | `""` | Default country chip used when no per-session browser preference exists |
| `leads_default_status` | str | `""` | Default status chip used when no per-session browser preference exists |
| `leads_default_category` | str | `""` | Default category chip used when no per-session browser preference exists |

DB settings are loaded at the start of every scrape run via `db.apply_settings_to_config()`, overriding the module-level defaults in `config/config.py`.

## Source Notes

- `bing` is the first no-key fallback source and is used when configured.
- `duckduckgo` remains available as a secondary no-key source.
- `brave` is skipped unless `BRAVE_SEARCH_API_KEY` is configured.
- `nominatim` is intended for business/location discovery and only returns URLs when OSM records include website tags.
- Search progress is now stored per `(keyword, source)` pair, so repeated runs can resume each connector independently.

## Adding a New Environment Variable

1. Add the variable to Vercel project settings.
2. Add a `os.environ.get(...)` line in `config/config.py`.
3. Update this file (`docs/configuration.md`).
4. If it changes architecture significantly, write an ADR in `docs/adr/`.
