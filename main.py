"""main.py — LeadScraper Monster (online edition).

FastAPI application serving:
  • API routes under /api/
  • A redirect from / to the React dashboard at /dashboard/

Deployed on Vercel with PostgreSQL (Prisma Data Platform).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel

import config.config as cfg
import db.postgres as db
from scraper.scraper import LeadScraper
from scraper.sources import available_sources

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="LeadScraper Monster", version="2.0.0", docs_url="/api/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Request / response models ─────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[int] = None

class ScrapeRequest(BaseModel):
    keywords: list[str]
    session_id: Optional[int] = None
    max_pages: Optional[int] = None
    target_new_leads: int = 0
    semantic_resume: bool = True
    similarity_threshold: float = 0.32

class SessionCreate(BaseModel):
    name: Optional[str] = None

class SessionRename(BaseModel):
    name: str

class ConfigUpdate(BaseModel):
    keywords: list[str]
    max_pages: int = 3
    target_new_leads: int = 0
    request_delay_seconds: float = 1.5
    ai_enrichment_enabled: bool = True
    ai_confidence_threshold: float = 0.0
    leads_default_country: str = ""
    leads_default_status: str = ""
    leads_default_category: str = ""


class LeadUpdate(BaseModel):
    contact_name: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    owner: Optional[str] = None
    last_touch: Optional[str] = None
    opt_out: Optional[bool] = None


class SearchHistoryResolveRequest(BaseModel):
    query: str
    similarity_threshold: float = 0.32


async def _ensure_schema() -> None:
    await db.init_db()

# ── OpenAI system prompt ──────────────────────────────────────────────────────
_SYSTEM_PROMPT = """You are LeadBot, an expert B2B lead generation strategist and analyst.

You help the user:
- Craft and refine search keyword strategies for target industries and geographies
- Analyse scraping results and interpret lead quality
- Read confidence scores (0.9+ = strong lead; <0.4 = weak/irrelevant)
- Suggest follow-up keyword sets, sources, and enrichment improvements
- Understand industry signals, company categories, and contact hierarchies

Context:
- Pipeline: DuckDuckGo/Brave/Nominatim -> aiohttp -> BeautifulSoup4 -> OpenAI enrichment -> PostgreSQL
- Lead schema: company_name, website, country, city, contact_name, role, email,
  phone, source_url, category, size_signals, notes, confidence, status, owner,
  last_touch, opt_out
- The user triggers scraping with /scrape -- you do not run it yourself
- You are embedded in a web UI (not a terminal)

When scrape results appear in the conversation, refer to them concretely.
Be concise, strategic, and actionable. Avoid generic advice."""

MAX_HISTORY_TURNS = 20
TRUNCATE_AT = 1200


def _build_openai_messages(turns: list[dict]) -> list[dict]:
    recent = turns[-MAX_HISTORY_TURNS:]
    messages = []
    for i, t in enumerate(recent):
        content = t["content"]
        is_recent = i >= len(recent) - 4
        if not is_recent and len(content) > TRUNCATE_AT:
            content = content[:TRUNCATE_AT] + "\n[...truncated...]"
        messages.append({"role": t["role"], "content": content})
    return messages


def _json_serial(obj):
    import datetime
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serialisable")


async def _resolve_session(session_id: Optional[int]) -> int:
    """Return a valid session id, creating one if the requested id is gone."""
    if session_id is not None:
        existing = await db.get_session(session_id)
        if existing:
            return session_id
        # Session doesn't exist (e.g. after DB reset) — fall through
    latest = await db.get_latest_session()
    if latest and db.should_resume(latest):
        return latest["id"]
    s = await db.create_session()
    return s["id"]


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    try:
        stats = await db.get_stats()
        return {"status": "ok", "db": "connected", **stats}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


# ── DB bootstrap ──────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_init_db() -> None:
    await _ensure_schema()

@app.post("/api/db/init")
async def db_init():
    """Initialise (or migrate) the database schema. Idempotent."""
    try:
        await _ensure_schema()
        return {"status": "ok", "message": "Schema initialised."}
    except Exception as exc:
        raise HTTPException(500, detail=str(exc))


@app.post("/api/db/reset")
async def db_reset():
    """Drop all tables and recreate from scratch.  Wipes all data."""
    try:
        await db.reset_db()
        return {"status": "ok", "message": "Database wiped and schema recreated."}
    except Exception as exc:
        raise HTTPException(500, detail=str(exc))


# ── Config ────────────────────────────────────────────────────────────────────

@app.get("/api/config")
async def get_config():
    try:
        s = await db.get_settings()
        return s
    except Exception:
        return db._default_settings()


@app.post("/api/config")
async def update_config(body: ConfigUpdate):
    s = body.model_dump()
    await db.save_settings(s)
    db.apply_settings_to_config(s)
    return {"status": "ok"}


# ── Sessions ──────────────────────────────────────────────────────────────────

@app.get("/api/sessions")
async def list_sessions():
    sessions = await db.list_sessions(20)
    return json.loads(json.dumps(sessions, default=_json_serial))


@app.post("/api/sessions")
async def create_session(body: SessionCreate):
    session = await db.create_session(body.name)
    return json.loads(json.dumps(dict(session), default=_json_serial))


@app.get("/api/sessions/{session_id}/history")
async def get_session_history(session_id: int, limit: int = 40):
    session = await db.get_session(session_id)
    if not session:
        raise HTTPException(404, detail="Session not found")
    turns = await db.get_turns(session_id, limit)
    return json.loads(json.dumps(turns, default=_json_serial))


@app.patch("/api/sessions/{session_id}/rename")
async def rename_session(session_id: int, body: SessionRename):
    await db.rename_session(session_id, body.name)
    return {"status": "ok"}


# ── Chat (SSE) ────────────────────────────────────────────────────────────────

@app.post("/api/chat")
async def chat(body: ChatRequest):
    """Stream an OpenAI chat response as Server-Sent Events."""
    from openai import AsyncOpenAI

    if not cfg.OPENAI_API_KEY:
        raise HTTPException(503, detail="OPENAI_API_KEY not configured.")

    session_id = await _resolve_session(body.session_id)

    turns = await db.get_turns(session_id)
    await db.add_turn(session_id, "user", body.message, "chat")

    messages = [{"role": "system", "content": _SYSTEM_PROMPT}] + \
               _build_openai_messages(turns) + \
               [{"role": "user", "content": body.message}]

    client = AsyncOpenAI(api_key=cfg.OPENAI_API_KEY)
    full_reply = []

    async def sse_stream():
        try:
            stream = await client.chat.completions.create(
                model=cfg.OPENAI_MODEL,
                messages=messages,
                stream=True,
                max_completion_tokens=600,
                temperature=0.4,
            )
            async for chunk in stream:
                token = chunk.choices[0].delta.content
                if token:
                    full_reply.append(token)
                    yield f"data: {json.dumps({'type':'token','content':token,'session_id':session_id})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type':'error','content':str(exc)})}\n\n"
            return

        reply_text = "".join(full_reply)
        await db.add_turn(session_id, "assistant", reply_text, "chat")
        yield f"data: {json.dumps({'type':'done','session_id':session_id})}\n\n"

    return StreamingResponse(sse_stream(), media_type="text/event-stream")


# ── Scrape (SSE) ──────────────────────────────────────────────────────────────

@app.post("/api/scrape")
async def scrape(body: ScrapeRequest):
    """Run the scraping pipeline and stream lead events as SSE."""
    try:
        await _ensure_schema()
        settings = await db.get_settings()
        db.apply_settings_to_config(settings)
    except Exception:
        pass

    if body.max_pages is not None:
        cfg.MAX_PAGES = body.max_pages
    if body.target_new_leads:
        cfg.TARGET_NEW_LEADS = body.target_new_leads

    session_id = await _resolve_session(body.session_id)

    keywords = body.keywords

    async def sse_stream():
        run_id = None
        pages_visited = leads_new = leads_duplicate = leads_discarded = 0
        sources = available_sources()

        try:
            visited = await db.get_visited_urls()
            existing_keys = await db.get_dedupe_keys()
            run_id = await db.start_run(session_id, keywords, sources)
            search_offsets: dict[str, dict[str, dict[str, Any]]] = {
                keyword: await db.get_search_progress(keyword) for keyword in keywords
            }
            resolved_progress: dict[str, dict[str, Any]] = {}
            for keyword in keywords:
                normalized_keyword = " ".join(keyword.lower().split())
                keyword_offsets = search_offsets.setdefault(keyword, {})
                exact_next_page = max(
                    (int(state.get("next_page", 0)) for state in keyword_offsets.values()),
                    default=0,
                )
                if exact_next_page > 0:
                    resolved_progress[keyword] = {
                        "query_text": normalized_keyword,
                        "matched_query": normalized_keyword,
                        "next_page": exact_next_page,
                        "match_type": "exact",
                        "similarity": 1.0,
                    }
                    continue

                if not body.semantic_resume:
                    continue

                resolved = await db.resolve_search_progress(
                    normalized_keyword,
                    similarity_threshold=body.similarity_threshold,
                )
                next_page = int(resolved.get("next_page", 0))
                if next_page <= 0:
                    continue

                resolved_progress[keyword] = resolved
                for source in sources:
                    keyword_offsets.setdefault(
                        source,
                        {"next_page": next_page, "exhausted": False},
                    )
        except Exception as exc:
            yield f"data: {json.dumps({'type':'error','content':f'Database setup failed: {exc}'})}\n\n"
            return

        async def on_lead(lead, sid):
            await db.insert_lead(lead, session_id=sid)
            await db.mark_visited(lead.source_url)

        async def on_progress(rid, pages, new):
            await db.update_run_progress(rid, pages, new)

        async def on_search_progress(
            keyword: str,
            source: str,
            next_page: int,
            exhausted: bool,
        ):
            normalized_keyword = " ".join(keyword.lower().split())
            await db.set_search_progress(normalized_keyword, source, next_page, exhausted)
            await db.set_semantic_search_progress(normalized_keyword, next_page, run_id)

        kw_str = ", ".join(keywords)
        for keyword in keywords:
            resolved = resolved_progress.get(keyword)
            if not resolved:
                continue
            next_page = int(resolved.get("next_page", 0))
            if next_page <= 0:
                continue
            if resolved.get("match_type") == "semantic":
                matched_query = resolved.get("matched_query", keyword)
                similarity = float(resolved.get("similarity", 0.0))
                payload = {
                    "type": "progress",
                    "msg": (
                        f"Semantic resume matched '{keyword}' to '{matched_query}' "
                        f"(similarity {similarity:.2f}); continuing from results page {next_page + 1}"
                    ),
                    "phase": "resume",
                }
                yield f"data: {json.dumps(payload)}\n\n"
            else:
                payload = {
                    "type": "progress",
                    "msg": f"Resuming '{keyword}' from results page {next_page + 1}",
                    "phase": "resume",
                }
                yield f"data: {json.dumps(payload)}\n\n"

        await db.add_turn(session_id, "user", f"/scrape {kw_str}", "scrape")

        scrape_lines = []

        try:
            async with LeadScraper() as scraper:
                async for event in scraper.run_streaming(
                    keywords=keywords,
                    visited=visited,
                    existing_keys=existing_keys,
                    on_lead=on_lead,
                    on_progress=on_progress,
                    search_offsets=search_offsets,
                    on_search_progress=on_search_progress,
                    target_new_leads=cfg.TARGET_NEW_LEADS,
                    run_id=run_id,
                    session_id=session_id,
                ):
                    payload = json.dumps({"type": event.type, **event.data})
                    yield f"data: {payload}\n\n"

                    if event.type == "lead":
                        scrape_lines.append(
                            f"- {event.data.get('company_name','?')[:30]}  "
                            f"{event.data.get('email','--')[:28]}  "
                            f"conf={event.data.get('confidence',0):.2f}"
                        )
                    elif event.type == "done":
                        pages_visited = event.data.get("pages_visited", 0)
                        leads_new = event.data.get("leads_new", 0)
                        leads_duplicate = event.data.get("leads_duplicate", 0)
                        leads_discarded = event.data.get("leads_discarded", 0)
        except Exception as exc:
            yield f"data: {json.dumps({'type':'error','content':str(exc)})}\n\n"

        if run_id:
            try:
                await db.finish_run(run_id, pages_visited, leads_new, leads_duplicate, leads_discarded)
            except Exception:
                pass

        summary = (
            f"Scrape complete -- {leads_new} new | {leads_duplicate} dup | "
            f"{leads_discarded} discarded | {pages_visited} pages\n"
        )
        if scrape_lines:
            summary += "\n".join(scrape_lines[:15])
        try:
            await db.add_turn(session_id, "assistant", summary, "scrape")
        except Exception:
            pass

    return StreamingResponse(sse_stream(), media_type="text/event-stream")


# ── Leads ─────────────────────────────────────────────────────────────────────

@app.get("/api/leads")
async def get_leads(
    page: int = Query(1, ge=1),
    page_size: int = Query(cfg.LEADS_PAGE_SIZE, ge=1, le=200),
    search: str = Query(""),
    include_archived: bool = Query(False),
    sort_by: str = Query("created_at"),
    sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
    country: str = Query(""),
    status: str = Query(""),
    category: str = Query(""),
):
    leads, total = await db.get_leads(
        page,
        page_size,
        search,
        include_archived,
        sort_by,
        sort_dir,
        country,
        status,
        category,
    )
    return json.loads(json.dumps({
        "leads": leads,
        "total": total,
        "page": page,
        "page_size": page_size,
        "sort_by": sort_by,
        "sort_dir": sort_dir,
    }, default=_json_serial))


@app.get("/api/leads/export")
async def export_leads():
    csv_data = await db.export_leads_csv()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads.csv"},
    )


@app.patch("/api/leads/{lead_id}/archive")
async def toggle_archive(lead_id: int, archived: bool = Query(True)):
    await db.archive_lead(lead_id, archived)
    return {"status": "ok"}


@app.patch("/api/leads/{lead_id}")
async def update_lead(lead_id: int, body: LeadUpdate):
    updates = {
        key: value
        for key, value in body.model_dump(exclude_unset=True).items()
        if value is not None
    }
    if not updates:
        return {"status": "ok", "updated": []}
    await db.update_lead(lead_id, updates)
    return {"status": "ok", "updated": sorted(updates.keys())}


# ── Runs / Stats ──────────────────────────────────────────────────────────────

@app.get("/api/runs")
async def get_runs(limit: int = Query(50, ge=1, le=200)):
    runs = await db.list_runs(limit)
    return json.loads(json.dumps(runs, default=_json_serial))


@app.get("/api/stats")
async def get_stats():
    try:
        return await db.get_stats()
    except Exception as exc:
        raise HTTPException(500, detail=str(exc))


@app.get("/api/search-history")
async def get_search_history(
    query: str = Query("", description="Keyword text for semantic lookup"),
    limit: int = Query(5, ge=1, le=20),
    similarity_threshold: float = Query(0.32, ge=0.0, le=1.0),
):
    await _ensure_schema()
    if not query.strip():
        return {"query": query, "matches": []}
    matches = await db.semantic_search_progress(
        query,
        limit=limit,
        similarity_threshold=similarity_threshold,
    )
    return {"query": query, "matches": json.loads(json.dumps(matches, default=_json_serial))}


@app.post("/api/search-history/resolve")
async def resolve_search_history(body: SearchHistoryResolveRequest):
    await _ensure_schema()
    resolved = await db.resolve_search_progress(
        body.query,
        similarity_threshold=body.similarity_threshold,
    )
    return json.loads(json.dumps(resolved, default=_json_serial))


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/dashboard/", status_code=307)
