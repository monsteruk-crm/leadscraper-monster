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
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import asyncpg

import config.config as cfg

logger = logging.getLogger(__name__)

# Module-level pool (re-used across warm Vercel invocations).
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(cfg.DATABASE_URL, min_size=1, max_size=5)
    return _pool


@asynccontextmanager
async def get_conn():
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn


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

CREATE TABLE IF NOT EXISTS settings (
    id                      INTEGER PRIMARY KEY DEFAULT 1,
    keywords                JSONB   NOT NULL DEFAULT '["sustainable packaging suppliers UK"]',
    max_pages               INTEGER NOT NULL DEFAULT 3,
    target_new_leads        INTEGER NOT NULL DEFAULT 0,
    request_delay_seconds   REAL    NOT NULL DEFAULT 1.5,
    ai_enrichment_enabled   BOOLEAN NOT NULL DEFAULT true,
    ai_confidence_threshold REAL    NOT NULL DEFAULT 0.0,
    CONSTRAINT settings_singleton CHECK (id = 1)
);

-- Ensure the settings row always exists
INSERT INTO settings (id) VALUES (1) ON CONFLICT DO NOTHING;
"""


async def init_db() -> None:
    """Idempotent schema bootstrap.  Safe to call on every startup."""
    async with get_conn() as conn:
        await conn.execute(_DDL)
    logger.info("Database schema initialised.")


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
                ai_confidence_threshold = $6
            WHERE id = 1
        """,
            json.dumps(s.get("keywords", ["sustainable packaging suppliers UK"])),
            int(s.get("max_pages", 3)),
            int(s.get("target_new_leads", 0)),
            float(s.get("request_delay_seconds", 1.5)),
            bool(s.get("ai_enrichment_enabled", True)),
            float(s.get("ai_confidence_threshold", 0.0)),
        )


def _default_settings() -> dict[str, Any]:
    return {
        "keywords": ["sustainable packaging suppliers UK"],
        "max_pages": 3,
        "target_new_leads": 0,
        "request_delay_seconds": 1.5,
        "ai_enrichment_enabled": True,
        "ai_confidence_threshold": 0.0,
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
        row = await conn.fetchrow("""
            INSERT INTO leads (
                company_name, website, country, city, contact_name, role,
                email, phone, source_url, category, size_signals, notes,
                confidence, status, owner, last_touch, opt_out,
                dedupe_key, session_id
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19
            ) RETURNING id
        """,
            lead.company_name, lead.website, lead.country, lead.city,
            lead.contact_name, lead.role, lead.email, lead.phone,
            lead.source_url, lead.category, lead.size_signals, lead.notes,
            lead.confidence, lead.status, lead.owner, lead.last_touch,
            bool(lead.opt_out), key, session_id,
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
) -> tuple[list[dict], int]:
    """Return (leads, total_count) for the given page."""
    offset = (page - 1) * page_size
    async with get_conn() as conn:
        base_where = "" if include_archived else "AND archived = false"
        if search:
            where = f"WHERE (company_name ILIKE $3 OR email ILIKE $3 OR category ILIKE $3 OR country ILIKE $3) {base_where}"
            args = [page_size, offset, f"%{search}%"]
        else:
            where = f"WHERE true {base_where}"
            args = [page_size, offset]

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM leads {where.replace('$3', '$1')}",
            *([f"%{search}%"] if search else []),
        )
        rows = await conn.fetch(
            f"SELECT * FROM leads {where} ORDER BY id DESC LIMIT $1 OFFSET $2",
            *args,
        )
        return [dict(r) for r in rows], total


async def archive_lead(lead_id: int, archived: bool = True) -> None:
    async with get_conn() as conn:
        await conn.execute(
            "UPDATE leads SET archived = $1 WHERE id = $2", archived, lead_id
        )


async def get_all_leads_for_export() -> list[dict]:
    async with get_conn() as conn:
        rows = await conn.fetch("SELECT * FROM leads ORDER BY id DESC")
        return [dict(r) for r in rows]


async def export_leads_csv() -> str:
    """Return a CSV string of all non-archived leads."""
    from scraper.models import Lead
    fieldnames = Lead.fieldnames()
    leads = await get_all_leads_for_export()
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for lead in leads:
        row = {k: lead.get(k, "") for k in fieldnames}
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
