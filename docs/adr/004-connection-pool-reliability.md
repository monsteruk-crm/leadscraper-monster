# 004 — asyncpg Connection Pool Reliability

**Status:** Accepted

## Context

Several related failures were observed in production and local development:

1. **Blank leads table after Reset DB** — `reset_db()` drops and recreates all tables. asyncpg caches prepared statements per connection. After the schema was recreated, cached INSERT plans pointed at the old (dropped) tables, causing every subsequent `insert_lead()` call to fail silently. Lead cards appeared in the chat (SSE events were emitted before the DB call) but nothing was persisted, so the leads drawer showed an empty table.

2. **FUNCTION_INVOCATION_FAILED on page reload** — `vercel dev` hot-reloads the Python module when source files change, but the module-level `_pool` variable persists in memory pointing to a closed/invalidated pool. The first request after a hot-reload hit the broken pool and crashed the ASGI process.

3. **FUNCTION_INVOCATION_FAILED on every `/api/leads` request** — A retry attempt was added to `get_conn()` inside an `@asynccontextmanager`. The retry tried to `yield` a second time within the same generator, which Python forbids. This `RuntimeError` manifested as a 500 on every leads request.

4. **ForeignKeyViolationError after Reset DB** — The browser held a stale `session_id` (e.g. `1`) from before the wipe. Both `/api/chat` and `/api/scrape` used it directly without checking it existed, causing `chat_turns` insert to violate the FK constraint on `sessions`.

## Decision

Four targeted fixes applied to `db/postgres.py` and `main.py`:

**1. Disable prepared-statement cache** (`statement_cache_size=0` on pool creation)
asyncpg caches query plans per connection by default. Setting `statement_cache_size=0` prevents stale plans after any DDL change (Reset DB or schema migration).

**2. `_close_pool()` called after `reset_db()`**
After dropping and recreating tables, the old pool is explicitly closed and set to `None`. The next request rebuilds it with no cached plans.

**3. `get_pool()` checks `pool._closed`**
If the pool was closed (hot-reload, explicit `_close_pool()`, or process restart), `get_pool()` automatically creates a new one rather than returning a dead object.

**4. `get_conn()` — no retry, just close-and-re-raise**
The broken retry (double-yield) was replaced with: catch `InterfaceError`/`OSError`, call `_close_pool()` so the *next* request reconnects cleanly, then re-raise the exception. The current request fails with a clear error rather than hanging.

**5. `_resolve_session()` in `main.py`**
Before using a `session_id` from the browser, check it exists in `sessions`. If not (e.g. after Reset DB), fall back to the latest resumable session or create a new one. This eliminates the FK violation crash.

**6. `on_lead` failures surfaced as SSE `warning` events**
Previously, DB insert failures in the scraper were silently logged. Now they emit a yellow `warning` SSE event in the chat bubble so the operator knows leads are not being persisted.

## Consequences

**Positive:**
- Reset DB → scrape → view leads works reliably without a server restart.
- Hot-reload during development no longer breaks subsequent requests.
- DB save failures are now visible in the UI rather than silent.
- `_resolve_session()` makes all session-sensitive endpoints resilient to DB wipes.

**Negative:**
- `statement_cache_size=0` means every query is sent to PostgreSQL without a cached plan. This adds a small per-query overhead (~1 ms) acceptable for this use case.
- Accessing `pool._closed` uses a private asyncpg attribute; if asyncpg changes its internals this check may need updating.
- On a connection error the current request fails (not retried). The operator may need to reload the page once after a disruption.
