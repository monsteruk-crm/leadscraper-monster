"""postgres.py — Async PostgreSQL persistence layer for LeadScraper (online edition).

Replaces both `scraper/database.py` (LeadsDB) and `scraper/memory.py` (AgentMemory)
from the local project.  Uses asyncpg for all operations.

Tables:
  sessions      — chat sessions
  chat_turns    — conversation turns per session
  leads         — scraped leads (unique on dedupe_key)
  visited_urls  — every URL ever fetched
  search_runs   — log of every scrape run
  settings      — single-row configuration (id=1)
"""

from __future__ import annotations

import csv
import io
import json
import logging
from collections.abc import Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import asyncpg

import config.config as cfg

logger = logging.getLogger(__name__)

LEAD_SHEET_FIELDNAMES = [
    "company_name",
    "website",
    "country",
    "city",
    "contact_name",
    "role",
    "email",
    "source_url",
    "category",
    "size/signals",
    "notes",
    "confidence",
    "status",
    "owner",
    "last_touch",
    "opt_out",
]

# Module-level pool (re-used across warm Vercel invocations).
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    # Recreate if pool was never created or has been closed (e.g. after
    # reset_db() or a vercel dev hot-reload that closed the old process).
    if _pool is None or getattr(_pool, '_closed', False):
        _pool = await asyncpg.create_pool(
            cfg.DATABASE_URL,
            min_size=1,
            max_size=5,
            statement_cache_size=0,  # prevents stale plans after DDL changes
        )
    return _pool


async def _close_pool() -> None:
    """Close and discard the module-level pool (call after DDL changes)."""
    global _pool
    if _pool is not None:
        try:
            await _pool.close()
        except Exception:
            pass
        _pool = None


@asynccontextmanager
async def get_conn():
    """Yield a DB connection.  On interface/OS errors, closes the pool so
    the *next* request gets a fresh one (we do not retry the current request
    to avoid double-yield issues with asynccontextmanager)."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            yield conn
    except (asyncpg.InterfaceError, OSError) as exc:
        logger.warning("Connection error (%s) — pool closed; next request will reconnect.", exc)
        await _close_pool()
        raise


# ── Schema bootstrap ─────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    id         SERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chat_turns (
    id         SERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    mode       TEXT NOT NULL DEFAULT 'chat',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chat_turns_session ON chat_turns(session_id);

CREATE TABLE IF NOT EXISTS leads (
    id           SERIAL PRIMARY KEY,
    company_name TEXT    DEFAULT '',
    website      TEXT    DEFAULT '',
    country      TEXT    DEFAULT '',
    city         TEXT    DEFAULT '',
    contact_name TEXT    DEFAULT '',
    role         TEXT    DEFAULT '',
    email        TEXT    DEFAULT '',
    phone        TEXT    DEFAULT '',
    source_url   TEXT    DEFAULT '',
    category     TEXT    DEFAULT '',
    size_signals TEXT    DEFAULT '',
    notes        TEXT    DEFAULT '',
    confidence   REAL    DEFAULT 0.0,
    status       TEXT    DEFAULT 'New',
    owner        TEXT    DEFAULT '',
    last_touch   TEXT    DEFAULT '',
    opt_out      BOOLEAN DEFAULT false,
    dedupe_key   TEXT    NOT NULL UNIQUE,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    session_id   INTEGER,
    archived     BOOLEAN DEFAULT false
);

CREATE TABLE IF NOT EXISTS visited_urls (
    id            SERIAL PRIMARY KEY,
    url           TEXT NOT NULL UNIQUE,
    first_seen_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_visited_url ON visited_urls(url);

CREATE TABLE IF NOT EXISTS search_runs (
    id               SERIAL PRIMARY KEY,
    session_id       INTEGER,
    keywords         JSONB NOT NULL DEFAULT '[]',
    pages_crawled    INTEGER DEFAULT 0,
    leads_new        INTEGER DEFAULT 0,
    leads_duplicate  INTEGER DEFAULT 0,
    leads_discarded  INTEGER DEFAULT 0,
    started_at       TIMESTAMPTZ DEFAULT NOW(),
    finished_at      TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS search_progress (
    keyword          TEXT PRIMARY KEY,
    next_page        INTEGER NOT NULL DEFAULT 0,
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_search_progress_updated ON search_progress(updated_at);

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS semantic_search_history (
    id               SERIAL PRIMARY KEY,
    query_text       TEXT NOT NULL UNIQUE,
    next_page        INTEGER NOT NULL DEFAULT 0,
    matched_runs     INTEGER NOT NULL DEFAULT 0,
    last_run_id      INTEGER,
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_semantic_history_query_trgm
    ON semantic_search_history USING gin (query_text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_semantic_history_updated ON semantic_search_history(updated_at);

CREATE TABLE IF NOT EXISTS settings (
    id                      INTEGER PRIMARY KEY DEFAULT 1,
    keywords                JSONB   NOT NULL DEFAULT '["sustainable packaging suppliers UK"]',
    max_pages               INTEGER NOT NULL DEFAULT 3,
    target_new_leads        INTEGER NOT NULL DEFAULT 0,
    request_delay_seconds   REAL    NOT NULL DEFAULT 1.5,
    ai_enrichment_enabled   BOOLEAN NOT NULL DEFAULT true,
    ai_confidence_threshold REAL    NOT NULL DEFAULT 0.0,
    leads_default_country   TEXT    NOT NULL DEFAULT '',
    leads_default_status    TEXT    NOT NULL DEFAULT '',
    leads_default_category  TEXT    NOT NULL DEFAULT '',
    CONSTRAINT settings_singleton CHECK (id = 1)
);

-- Ensure the settings row exists (do not overwrite user-saved values on re-init)
INSERT INTO settings (
    id, keywords, max_pages, target_new_leads,
    request_delay_seconds, ai_enrichment_enabled, ai_confidence_threshold,
    leads_default_country, leads_default_status, leads_default_category
) VALUES (
    1,
    '["sustainable packaging suppliers UK",
      "eco packaging manufacturer UK",
      "B2B packaging solutions England",
      "green packaging company UK"]',
    3, 0, 1.5, true, 0.3, '', '', ''
) ON CONFLICT DO NOTHING;
"""


_DROP_DDL = """
DROP TABLE IF EXISTS chat_turns, leads, visited_urls, search_runs, search_progress, semantic_search_history, settings, sessions CASCADE;
"""

# After a full reset we always restore settings to a clean seed (UPSERT).
_SEED_SETTINGS = """
INSERT INTO settings (
    id, keywords, max_pages, target_new_leads,
    request_delay_seconds, ai_enrichment_enabled, ai_confidence_threshold,
    leads_default_country, leads_default_status, leads_default_category
) VALUES (
    1,
    '["sustainable packaging suppliers UK",
      "eco packaging manufacturer UK",
      "B2B packaging solutions England",
      "green packaging company UK"]',
    3, 0, 1.5, true, 0.3, '', '', ''
) ON CONFLICT (id) DO UPDATE SET
    keywords                = EXCLUDED.keywords,
    max_pages               = EXCLUDED.max_pages,
    target_new_leads        = EXCLUDED.target_new_leads,
    request_delay_seconds   = EXCLUDED.request_delay_seconds,
    ai_enrichment_enabled   = EXCLUDED.ai_enrichment_enabled,
    ai_confidence_threshold = EXCLUDED.ai_confidence_threshold,
    leads_default_country   = EXCLUDED.leads_default_country,
    leads_default_status    = EXCLUDED.leads_default_status,
    leads_default_category  = EXCLUDED.leads_default_category;
"""


async def init_db() -> None:
    """Idempotent schema bootstrap.  Safe to call on every startup."""
    async with get_conn() as conn:
        await conn.execute(_DDL)
    logger.info("Database schema initialised.")


async def reset_db() -> None:
    """Drop all tables, recreate from scratch, and restore seed settings."""
    async with get_conn() as conn:
        await conn.execute(_DROP_DDL)
        await conn.execute(_DDL)
        await conn.execute(_SEED_SETTINGS)
    # Discard the pool so the next request gets fresh connections with no
    # cached prepared statements pointing at the old (dropped) schema.
    await _close_pool()
    logger.info("Database reset and schema recreated.")


# ── Settings ─────────────────────────────────────────────────────────────────

async def get_settings() -> dict[str, Any]:
    async with get_conn() as conn:
        row = await conn.fetchrow("SELECT * FROM settings WHERE id = 1")
        if row is None:
            return _default_settings()
        d = dict(row)
        d["keywords"] = d["keywords"] if isinstance(d["keywords"], list) else json.loads(d["keywords"])
        return d


async def save_settings(s: dict[str, Any]) -> None:
    async with get_conn() as conn:
        await conn.execute("""
            UPDATE settings SET
                keywords                = $1,
                max_pages               = $2,
                target_new_leads        = $3,
                request_delay_seconds   = $4,
                ai_enrichment_enabled   = $5,
                ai_confidence_threshold = $6,
                leads_default_country   = $7,
                leads_default_status    = $8,
                leads_default_category  = $9
            WHERE id = 1
        """,
            json.dumps(s.get("keywords", _default_settings()["keywords"])),
            int(s.get("max_pages", 3)),
            int(s.get("target_new_leads", 0)),
            float(s.get("request_delay_seconds", 1.5)),
            bool(s.get("ai_enrichment_enabled", True)),
            float(s.get("ai_confidence_threshold", 0.0)),
            str(s.get("leads_default_country", "")),
            str(s.get("leads_default_status", "")),
            str(s.get("leads_default_category", "")),
        )


def _default_settings() -> dict[str, Any]:
    return {
        "keywords": [
            "sustainable packaging suppliers UK",
            "eco packaging manufacturer UK",
            "B2B packaging solutions England",
            "green packaging company UK",
        ],
        "max_pages": 3,
        "target_new_leads": 0,
        "request_delay_seconds": 1.5,
        "ai_enrichment_enabled": True,
        "ai_confidence_threshold": 0.3,
        "leads_default_country": "",
        "leads_default_status": "",
        "leads_default_category": "",
    }


def apply_settings_to_config(s: dict[str, Any]) -> None:
    """Mutate the live config module with loaded settings."""
    cfg.SEARCH_KEYWORDS = s.get("keywords", cfg.SEARCH_KEYWORDS)
    cfg.MAX_PAGES = int(s.get("max_pages", cfg.MAX_PAGES))
    cfg.REQUEST_DELAY_SECONDS = float(s.get("request_delay_seconds", cfg.REQUEST_DELAY_SECONDS))
    cfg.AI_ENRICHMENT_ENABLED = bool(s.get("ai_enrichment_enabled", cfg.AI_ENRICHMENT_ENABLED))
    cfg.AI_CONFIDENCE_THRESHOLD = float(s.get("ai_confidence_threshold", cfg.AI_CONFIDENCE_THRESHOLD))
    cfg.TARGET_NEW_LEADS = int(s.get("target_new_leads", cfg.TARGET_NEW_LEADS))


# ── Sessions ─────────────────────────────────────────────────────────────────

async def create_session(name: Optional[str] = None) -> dict:
    resolved = name or datetime.now().strftime("Session %Y-%m-%d %H:%M")
    async with get_conn() as conn:
        row = await conn.fetchrow(
            "INSERT INTO sessions (name) VALUES ($1) RETURNING *", resolved
        )
        return dict(row)


async def get_latest_session() -> Optional[dict]:
    async with get_conn() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT 1"
        )
        return dict(row) if row else None


async def get_session(session_id: int) -> Optional[dict]:
    async with get_conn() as conn:
        row = await conn.fetchrow("SELECT * FROM sessions WHERE id = $1", session_id)
        return dict(row) if row else None


async def list_sessions(limit: int = 20) -> list[dict]:
    async with get_conn() as conn:
        rows = await conn.fetch("""
            SELECT s.id, s.name, s.updated_at,
                   COUNT(t.id)::int AS turn_count
            FROM sessions s
            LEFT JOIN chat_turns t ON t.session_id = s.id
            GROUP BY s.id
            ORDER BY s.updated_at DESC
            LIMIT $1
        """, limit)
        return [dict(r) for r in rows]


async def rename_session(session_id: int, name: str) -> None:
    async with get_conn() as conn:
        await conn.execute(
            "UPDATE sessions SET name = $1, updated_at = NOW() WHERE id = $2",
            name, session_id,
        )


async def touch_session(session_id: int) -> None:
    async with get_conn() as conn:
        await conn.execute(
            "UPDATE sessions SET updated_at = NOW() WHERE id = $1", session_id
        )


def should_resume(session: dict, max_age_hours: int = 4) -> bool:
    updated = session.get("updated_at")
    if updated is None:
        return False
    if isinstance(updated, str):
        updated = datetime.fromisoformat(updated)
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - updated < timedelta(hours=max_age_hours)


# ── Chat turns ────────────────────────────────────────────────────────────────

async def add_turn(session_id: int, role: str, content: str, mode: str = "chat") -> None:
    async with get_conn() as conn:
        await conn.execute(
            "INSERT INTO chat_turns (session_id, role, content, mode) VALUES ($1,$2,$3,$4)",
            session_id, role, content, mode,
        )
    await touch_session(session_id)


async def get_turns(session_id: int, limit: int = 40) -> list[dict]:
    async with get_conn() as conn:
        rows = await conn.fetch("""
            SELECT * FROM (
                SELECT * FROM chat_turns WHERE session_id = $1 ORDER BY id DESC LIMIT $2
            ) sub ORDER BY id ASC
        """, session_id, limit)
        return [dict(r) for r in rows]


async def search_turns(query: str, limit: int = 10) -> list[dict]:
    async with get_conn() as conn:
        rows = await conn.fetch("""
            SELECT t.*, s.name AS session_name
            FROM chat_turns t
            JOIN sessions s ON t.session_id = s.id
            WHERE t.content ILIKE $1
            ORDER BY t.id DESC
            LIMIT $2
        """, f"%{query}%", limit)
        return [dict(r) for r in rows]


# ── Leads ─────────────────────────────────────────────────────────────────────

async def insert_lead(lead: "Lead", session_id: Optional[int] = None) -> tuple[int, bool]:  # type: ignore[name-defined]
    """Insert a lead. Returns (id, is_new)."""
    from scraper.models import Lead  # avoid circular import at module level
    key = lead.dedupe_key
    async with get_conn() as conn:
        existing = await conn.fetchrow(
            "SELECT id FROM leads WHERE dedupe_key = $1", key
        )
        if existing:
            return existing["id"], False
        contact_name = (
            lead.contact_name
            or " ".join(part for part in [lead.first_name, lead.last_name] if part).strip()
        )
        role = lead.role or lead.title
        row = await conn.fetchrow("""
            INSERT INTO leads (
                company_name, website, country, city,
                contact_name, role,
                email, phone, source_url, category, size_signals, notes,
                confidence, status, owner, last_touch, opt_out,
                dedupe_key, session_id
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19
            ) RETURNING id
        """,
            lead.company_name, lead.website, lead.country, lead.city,
            contact_name, role,
            lead.email, lead.phone, lead.source_url, lead.category,
            lead.size_signals, lead.notes, lead.confidence, lead.status,
            lead.owner, lead.last_touch, bool(lead.opt_out), key, session_id,
        )
        return row["id"], True


async def get_dedupe_keys() -> set[str]:
    async with get_conn() as conn:
        rows = await conn.fetch("SELECT dedupe_key FROM leads")
        return {r["dedupe_key"] for r in rows}


async def get_leads_count() -> int:
    async with get_conn() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM leads")


async def get_leads(
    page: int = 1,
    page_size: int = 50,
    search: str = "",
    include_archived: bool = False,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    country_filter: str = "",
    status_filter: str = "",
    category_filter: str = "",
) -> tuple[list[dict], int]:
    """Return (leads, total_count) for the given page."""
    offset = (page - 1) * page_size
    sort_columns = {
        "company_name": "company_name",
        "contact_name": "contact_name",
        "role": "role",
        "email": "email",
        "country": "country",
        "city": "city",
        "category": "category",
        "confidence": "confidence",
        "status": "status",
        "created_at": "created_at",
    }
    resolved_sort_by = sort_columns.get(sort_by, "created_at")
    resolved_sort_dir = "ASC" if sort_dir.lower() == "asc" else "DESC"
    order_clause = f"ORDER BY {resolved_sort_by} {resolved_sort_dir}, id DESC"
    async with get_conn() as conn:
        filters: list[str] = []
        filter_args: list[Any] = []

        if not include_archived:
            filters.append("archived = false")

        if search:
            filter_args.append(f"%{search}%")
            idx = len(filter_args)
            filters.append(
                "("
                f"company_name ILIKE ${idx} OR contact_name ILIKE ${idx} OR role ILIKE ${idx} OR "
                f"email ILIKE ${idx} OR category ILIKE ${idx} OR country ILIKE ${idx} OR city ILIKE ${idx} "
                f"OR status ILIKE ${idx}"
                ")"
            )

        if country_filter:
            filter_args.append(country_filter)
            filters.append(f"country = ${len(filter_args)}")

        if status_filter:
            filter_args.append(status_filter)
            filters.append(f"status = ${len(filter_args)}")

        if category_filter:
            filter_args.append(category_filter)
            filters.append(f"category = ${len(filter_args)}")

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM leads {where_clause}",
            *filter_args,
        )
        page_args = filter_args + [page_size, offset]
        rows = await conn.fetch(
            f"SELECT * FROM leads {where_clause} {order_clause} LIMIT ${len(page_args) - 1} OFFSET ${len(page_args)}",
            *page_args,
        )
        return [dict(r) for r in rows], total


async def archive_lead(lead_id: int, archived: bool = True) -> None:
    async with get_conn() as conn:
        await conn.execute(
            "UPDATE leads SET archived = $1 WHERE id = $2", archived, lead_id
        )


async def update_lead(lead_id: int, fields: Mapping[str, Any]) -> None:
    allowed = {
        "contact_name": "contact_name",
        "role": "role",
        "status": "status",
        "notes": "notes",
        "owner": "owner",
        "last_touch": "last_touch",
        "opt_out": "opt_out",
    }
    updates: list[tuple[str, Any]] = []
    for key, value in fields.items():
        column = allowed.get(key)
        if column is not None:
            updates.append((column, value))

    if not updates:
        return

    assignments = ", ".join(f"{column} = ${index}" for index, (column, _) in enumerate(updates, start=1))
    values = [value for _, value in updates]
    async with get_conn() as conn:
        await conn.execute(
            f"UPDATE leads SET {assignments} WHERE id = ${len(values) + 1}",
            *values,
            lead_id,
        )


async def get_all_leads_for_export() -> list[dict]:
    async with get_conn() as conn:
        rows = await conn.fetch("SELECT * FROM leads ORDER BY id DESC")
        return [dict(r) for r in rows]


async def export_leads_csv() -> str:
    """Return a CSV string aligned to the documented lead sheet schema."""
    leads = await get_all_leads_for_export()
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=LEAD_SHEET_FIELDNAMES)
    writer.writeheader()
    for lead in leads:
        row = {k: lead.get(k, "") for k in LEAD_SHEET_FIELDNAMES}
        row["size/signals"] = lead.get("size_signals", "")
        row["role"] = lead.get("role", "") or lead.get("title", "")
        row["contact_name"] = lead.get("contact_name", "")
        row["opt_out"] = bool(row.get("opt_out", False))
        writer.writerow(row)
    return buf.getvalue()


# ── Visited URLs ──────────────────────────────────────────────────────────────

async def get_visited_urls() -> set[str]:
    async with get_conn() as conn:
        rows = await conn.fetch("SELECT url FROM visited_urls")
        return {r["url"] for r in rows}


async def mark_visited(url: str) -> None:
    async with get_conn() as conn:
        await conn.execute(
            "INSERT INTO visited_urls (url) VALUES ($1) ON CONFLICT DO NOTHING", url
        )


async def get_visited_count() -> int:
    async with get_conn() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM visited_urls")


# ── Search runs ───────────────────────────────────────────────────────────────

async def start_run(session_id: Optional[int], keywords: list[str]) -> int:
    async with get_conn() as conn:
        row = await conn.fetchrow(
            "INSERT INTO search_runs (session_id, keywords) VALUES ($1, $2) RETURNING id",
            session_id, json.dumps(keywords),
        )
        return row["id"]


async def update_run_progress(run_id: int, pages_crawled: int, leads_new: int) -> None:
    async with get_conn() as conn:
        await conn.execute(
            "UPDATE search_runs SET pages_crawled=$1, leads_new=$2 WHERE id=$3",
            pages_crawled, leads_new, run_id,
        )


async def finish_run(
    run_id: int,
    pages_crawled: int,
    leads_new: int,
    leads_duplicate: int,
    leads_discarded: int = 0,
) -> None:
    async with get_conn() as conn:
        await conn.execute("""
            UPDATE search_runs
            SET pages_crawled=$1, leads_new=$2, leads_duplicate=$3,
                leads_discarded=$4, finished_at=NOW()
            WHERE id=$5
        """, pages_crawled, leads_new, leads_duplicate, leads_discarded, run_id)


async def get_search_progress(keyword: str) -> int:
    async with get_conn() as conn:
        value = await conn.fetchval(
            "SELECT next_page FROM search_progress WHERE keyword = $1",
            keyword,
        )
        return int(value or 0)


async def set_search_progress(keyword: str, next_page: int) -> None:
    async with get_conn() as conn:
        await conn.execute(
            """
            INSERT INTO search_progress (keyword, next_page, updated_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (keyword) DO UPDATE SET
                next_page = EXCLUDED.next_page,
                updated_at = NOW()
            """,
            keyword,
            max(0, int(next_page)),
        )




async def set_semantic_search_progress(
    query_text: str,
    next_page: int,
    run_id: Optional[int] = None,
) -> None:
    normalized = " ".join(query_text.lower().split())
    if not normalized:
        return

    async with get_conn() as conn:
        await conn.execute(
            """
            INSERT INTO semantic_search_history (query_text, next_page, matched_runs, last_run_id, updated_at)
            VALUES ($1, $2, 1, $3, NOW())
            ON CONFLICT (query_text) DO UPDATE SET
                next_page = EXCLUDED.next_page,
                matched_runs = semantic_search_history.matched_runs + 1,
                last_run_id = COALESCE(EXCLUDED.last_run_id, semantic_search_history.last_run_id),
                updated_at = NOW()
            """,
            normalized,
            max(0, int(next_page)),
            run_id,
        )


async def semantic_search_progress(
    query_text: str,
    limit: int = 5,
    similarity_threshold: float = 0.32,
) -> list[dict[str, Any]]:
    normalized = " ".join(query_text.lower().split())
    if not normalized:
        return []

    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT
                query_text,
                next_page,
                matched_runs,
                last_run_id,
                updated_at,
                similarity(query_text, $1) AS similarity
            FROM semantic_search_history
            WHERE query_text % $1 OR similarity(query_text, $1) >= $2
            ORDER BY similarity DESC, updated_at DESC
            LIMIT $3
            """,
            normalized,
            similarity_threshold,
            limit,
        )
        return [dict(r) for r in rows]


async def resolve_search_progress(
    query_text: str,
    similarity_threshold: float = 0.32,
) -> dict[str, Any]:
    normalized = " ".join(query_text.lower().split())
    exact_next_page = await get_search_progress(normalized)
    if exact_next_page > 0:
        return {
            "query_text": normalized,
            "matched_query": normalized,
            "next_page": exact_next_page,
            "match_type": "exact",
            "similarity": 1.0,
        }

    matches = await semantic_search_progress(
        normalized,
        limit=1,
        similarity_threshold=similarity_threshold,
    )
    if not matches:
        return {
            "query_text": normalized,
            "matched_query": normalized,
            "next_page": 0,
            "match_type": "none",
            "similarity": 0.0,
        }

    top = matches[0]
    return {
        "query_text": normalized,
        "matched_query": top["query_text"],
        "next_page": int(top["next_page"] or 0),
        "match_type": "semantic",
        "similarity": float(top.get("similarity") or 0.0),
    }

async def list_runs(limit: int = 50) -> list[dict]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT * FROM search_runs ORDER BY id DESC LIMIT $1", limit
        )
        result = []
        for r in rows:
            d = dict(r)
            kw = d.get("keywords")
            if isinstance(kw, str):
                try:
                    d["keywords"] = json.loads(kw)
                except Exception:
                    d["keywords"] = [kw]
            elif kw is None:
                d["keywords"] = []
            result.append(d)
        return result


# ── Stats helper ──────────────────────────────────────────────────────────────

async def get_stats() -> dict:
    async with get_conn() as conn:
        leads = await conn.fetchval("SELECT COUNT(*) FROM leads") or 0
        visited = await conn.fetchval("SELECT COUNT(*) FROM visited_urls") or 0
        runs = await conn.fetchval("SELECT COUNT(*) FROM search_runs") or 0
        sessions = await conn.fetchval("SELECT COUNT(*) FROM sessions") or 0
    return {
        "leads": int(leads),
        "visited_urls": int(visited),
        "runs": int(runs),
        "sessions": int(sessions),
    }
