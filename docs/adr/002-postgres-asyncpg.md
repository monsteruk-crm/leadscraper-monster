# 002 — PostgreSQL + asyncpg Persistence

**Status:** Accepted

## Context

The original local project used SQLite (via a custom `LeadsDB` class) and CSV files for lead storage, and a JSON file for agent memory. Neither works on a stateless serverless platform where the filesystem is ephemeral.

A persistent external database was needed. Options considered:
- **Supabase** (managed Postgres, REST API)
- **PlanetScale** (managed MySQL)
- **Neon** (serverless Postgres)
- **Vercel Postgres** (now Neon-backed)
- Raw asyncpg connecting to any PostgreSQL host

## Decision

Use **PostgreSQL** accessed via **asyncpg** (raw async driver). The `DATABASE_URL` environment variable points to any PostgreSQL-compatible host (Neon, Supabase, Railway, etc.).

Reasons:
- asyncpg is the fastest Python PostgreSQL driver; avoids sync blocking in async FastAPI handlers.
- No ORM overhead — the schema is simple and hand-written DDL is easier to reason about and migrate.
- A module-level connection pool (`asyncpg.Pool`) is created once and reused across warm Vercel invocations, minimising connection overhead.
- PostgreSQL is widely supported across managed hosting providers; the `DATABASE_URL` abstraction keeps the code host-agnostic.

## Consequences

**Positive:**
- Full async I/O throughout the request path (no `run_in_executor` needed for DB calls).
- Schema is explicit and version-controlled in `db/postgres.py`.
- `init_db()` is idempotent (`CREATE TABLE IF NOT EXISTS`); safe to call on startup.
- `reset_db()` provides a clean-slate option when the schema changes.

**Negative:**
- Hand-written SQL means no automatic migration tooling; schema changes require manually updating the DDL and potentially calling `reset_db()` (losing data) or writing `ALTER TABLE` statements.
- asyncpg has no ORM — column order in INSERT statements must be kept in sync with the `Lead` dataclass manually.
- Connection pooling across serverless invocations can cause "too many connections" on free-tier PostgreSQL if many concurrent instances spin up; pool size is capped at 5 (`max_size=5`).
