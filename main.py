"""main.py — LeadScraper Monster (online edition).

FastAPI application serving:
  • A dark-theme SPA chat UI at /
  • All REST + SSE API routes under /api/

Deployed on Vercel with PostgreSQL (Prisma Data Platform).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel

import config.config as cfg
import db.postgres as db
from scraper.scraper import LeadScraper

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

# ── OpenAI system prompt ──────────────────────────────────────────────────────
_SYSTEM_PROMPT = """You are LeadBot, an expert B2B lead generation strategist and analyst.

You help the user:
- Craft and refine search keyword strategies for target industries and geographies
- Analyse scraping results and interpret lead quality
- Read confidence scores (0.9+ = strong lead; <0.4 = weak/irrelevant)
- Suggest follow-up keyword sets, sources, and enrichment improvements
- Understand industry signals, company categories, and contact hierarchies

Context:
- Pipeline: DuckDuckGo -> aiohttp -> BeautifulSoup4 -> OpenAI enrichment -> PostgreSQL
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

@app.post("/api/db/init")
async def db_init():
    """Initialise (or migrate) the database schema. Idempotent."""
    try:
        await db.init_db()
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
                max_tokens=600,
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

        try:
            visited = await db.get_visited_urls()
            existing_keys = await db.get_dedupe_keys()
            run_id = await db.start_run(session_id, keywords)
            search_offsets = {keyword: await db.get_search_progress(keyword) for keyword in keywords}
        except Exception as exc:
            yield f"data: {json.dumps({'type':'error','content':f'DB init failed: {exc}'})}\n\n"
            return

        async def on_lead(lead, sid):
            await db.insert_lead(lead, session_id=sid)
            await db.mark_visited(lead.source_url)

        async def on_progress(rid, pages, new):
            await db.update_run_progress(rid, pages, new)

        async def on_search_progress(keyword: str, next_page: int):
            await db.set_search_progress(keyword, next_page)

        kw_str = ", ".join(keywords)
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


# ── SPA ───────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse(content=_SPA_HTML)


_SPA_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LeadBot — B2B Lead Generation</title>
<link rel="icon" href="/favicon.ico">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#000;--bg1:#0a0a0a;--bg2:#111;--bg3:#1a1a1a;
  --border:#2a2a2a;--border2:#333;
  --text:#f0f0f0;--text2:#aaa;--text3:#666;
  --accent:#0070f3;--accent2:#005bb5;
  --green:#00c853;--red:#ff4444;--yellow:#f5a623;
  --radius:8px;--font:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif
}
body{background:var(--bg);color:var(--text);font-family:var(--font);height:100vh;display:flex;flex-direction:column;overflow:hidden}
header{border-bottom:1px solid var(--border);padding:.75rem 1.25rem;display:flex;align-items:center;gap:1rem;flex-shrink:0;z-index:100;background:var(--bg)}
.logo{font-size:1.1rem;font-weight:700;color:#fff;letter-spacing:-.5px}
.logo span{color:var(--accent)}
.session-badge{background:var(--bg2);border:1px solid var(--border2);border-radius:20px;padding:.2rem .75rem;font-size:.75rem;color:var(--text2);cursor:pointer;transition:.2s}
.session-badge:hover{border-color:#555;color:#fff}
.header-right{margin-left:auto;display:flex;gap:.5rem;align-items:center}
.btn{padding:.35rem .85rem;border-radius:6px;border:1px solid var(--border2);background:var(--bg2);color:var(--text);font-size:.8rem;cursor:pointer;transition:.2s;white-space:nowrap}
.btn:hover{background:var(--bg3);border-color:#555}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.btn.primary:hover{background:var(--accent2)}
.layout{display:flex;flex:1;overflow:hidden}
.sidebar{width:240px;border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden;flex-shrink:0}
.sidebar-section{padding:.75rem 1rem;border-bottom:1px solid var(--border)}
.sidebar-title{font-size:.7rem;color:var(--text3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:.5rem;font-weight:600}
.stat-row{display:flex;justify-content:space-between;font-size:.8rem;color:var(--text2);margin-bottom:.3rem}
.stat-val{color:#fff;font-weight:500}
.session-list{flex:1;overflow-y:auto;padding:.5rem 0}
.session-item{padding:.5rem 1rem;font-size:.82rem;cursor:pointer;color:var(--text2);border-left:2px solid transparent;transition:.15s;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.session-item:hover{background:var(--bg2);color:#fff}
.session-item.active{border-left-color:var(--accent);color:#fff;background:var(--bg2)}
.sidebar-actions{padding:.75rem;border-top:1px solid var(--border);display:flex;gap:.5rem;flex-wrap:wrap}
.sidebar-actions .btn{flex:1;text-align:center;font-size:.75rem}
.chat-container{flex:1;display:flex;flex-direction:column;overflow:hidden}
.messages{flex:1;overflow-y:auto;padding:1.25rem;display:flex;flex-direction:column;gap:.75rem}
.msg{display:flex;gap:.75rem;max-width:100%}
.msg.user{flex-direction:row-reverse}
.msg-bubble{max-width:72%;padding:.75rem 1rem;border-radius:var(--radius);font-size:.875rem;line-height:1.55;word-break:break-word;white-space:pre-wrap}
.msg.assistant .msg-bubble{background:var(--bg2);border:1px solid var(--border)}
.msg.user .msg-bubble{background:var(--accent);color:#fff}
.msg.scrape .msg-bubble{background:var(--bg3);border:1px solid var(--border2);font-family:monospace;font-size:.8rem}
.msg-avatar{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:.8rem;flex-shrink:0;margin-top:.1rem}
.msg.assistant .msg-avatar{background:#1a1a2e;border:1px solid var(--border2)}
.msg.user .msg-avatar{background:var(--accent2)}
.cursor{display:inline-block;width:2px;height:1em;background:var(--accent);animation:blink .7s step-end infinite;margin-left:1px;vertical-align:text-bottom}
@keyframes blink{50%{opacity:0}}
.lead-card{background:var(--bg3);border:1px solid var(--border2);border-radius:6px;padding:.6rem .85rem;font-size:.78rem;margin:.15rem 0;display:flex;flex-direction:column;gap:.15rem}
.lead-card strong{color:#fff;font-size:.82rem}
.lead-card .meta{color:var(--text2);font-size:.75rem}
.conf-badge{display:inline-block;padding:.05rem .35rem;border-radius:10px;font-size:.7rem;margin-left:.35rem}
.conf-high{background:#004d20;color:var(--green)}
.conf-mid{background:#3d2a00;color:var(--yellow)}
.conf-low{background:#2d0000;color:var(--red)}
.progress-line{font-size:.75rem;color:var(--text3);font-style:italic;margin:.1rem 0}
.input-area{border-top:1px solid var(--border);padding:.75rem 1.25rem;display:flex;flex-direction:column;gap:.5rem;background:var(--bg)}
.cmd-hint{display:flex;gap:.4rem;flex-wrap:wrap}
.cmd-pill{padding:.18rem .6rem;background:var(--bg2);border:1px solid var(--border2);border-radius:12px;font-size:.72rem;color:var(--text3);cursor:pointer;transition:.15s}
.cmd-pill:hover{border-color:#555;color:var(--text)}
.input-row{display:flex;gap:.6rem;align-items:center}
#msg-input{flex:1;background:var(--bg2);border:1px solid var(--border2);border-radius:var(--radius);padding:.6rem .9rem;color:#fff;font-size:.9rem;font-family:var(--font);outline:none;transition:.2s;resize:none;min-height:38px;max-height:120px}
#msg-input:focus{border-color:var(--accent)}
#send-btn{padding:.6rem 1.1rem;background:var(--accent);border:none;border-radius:var(--radius);color:#fff;font-size:.9rem;cursor:pointer;transition:.2s;flex-shrink:0}
#send-btn:hover{background:var(--accent2)}
#send-btn:disabled{opacity:.4;cursor:default}
.drawer-overlay{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:200;display:none}
.drawer-overlay.open{display:block}
.drawer{position:fixed;bottom:0;left:0;right:0;height:70vh;background:var(--bg1);border-top:1px solid var(--border);z-index:201;display:flex;flex-direction:column;transform:translateY(100%);transition:.3s}
.drawer.open{transform:translateY(0)}
.drawer-header{padding:.75rem 1.25rem;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:.75rem;flex-shrink:0}
.drawer-header h2{font-size:1rem;font-weight:600}
.drawer-search{flex:1;background:var(--bg2);border:1px solid var(--border2);border-radius:6px;padding:.4rem .75rem;color:#fff;font-size:.85rem;outline:none}
.drawer-search:focus{border-color:var(--accent)}
.leads-table-wrap{flex:1;overflow:auto}
table{width:100%;border-collapse:collapse;font-size:.8rem}
thead th{background:var(--bg2);padding:.5rem .75rem;text-align:left;color:var(--text2);font-weight:500;position:sticky;top:0;white-space:nowrap;border-bottom:1px solid var(--border)}
tbody tr{border-bottom:1px solid var(--border)}
tbody tr:hover{background:var(--bg2)}
tbody td{padding:.4rem .75rem;color:var(--text);max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.drawer-footer{padding:.6rem 1.25rem;border-top:1px solid var(--border);display:flex;align-items:center;gap:.75rem;flex-shrink:0;font-size:.82rem;color:var(--text2)}
.pagination{display:flex;gap:.4rem;margin-left:auto}
.page-btn{padding:.25rem .6rem;background:var(--bg2);border:1px solid var(--border2);border-radius:4px;color:var(--text);cursor:pointer;font-size:.78rem}
.page-btn:hover{background:var(--bg3)}
.page-btn.active{background:var(--accent);border-color:var(--accent)}
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:300;display:none;align-items:center;justify-content:center}
.modal-overlay.open{display:flex}
.modal{background:var(--bg2);border:1px solid var(--border2);border-radius:var(--radius);padding:1.5rem;width:min(480px,90vw);display:flex;flex-direction:column;gap:1rem}
.modal h3{font-size:1rem;font-weight:600}
.form-group{display:flex;flex-direction:column;gap:.4rem}
.form-group label{font-size:.8rem;color:var(--text2)}
.form-group input,.form-group textarea,.form-group select{background:var(--bg3);border:1px solid var(--border2);border-radius:6px;padding:.5rem .75rem;color:#fff;font-size:.875rem;outline:none;font-family:var(--font)}
.form-group input:focus,.form-group textarea:focus{border-color:var(--accent)}
.form-row{display:flex;gap:.75rem}
.form-row .form-group{flex:1}
.modal-actions{display:flex;gap:.6rem;justify-content:flex-end}
::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--border2);border-radius:3px}
@media(max-width:700px){.sidebar{display:none}.msg-bubble{max-width:90%}}
</style>
</head>
<body>
<header>
  <div class="logo">Lead<span>Bot</span></div>
  <div id="session-badge" class="session-badge" onclick="openSessionModal()">Loading...</div>
  <div class="header-right">
    <button class="btn" onclick="openLeadsDrawer()">Leads <span id="leads-count-badge">-</span></button>
    <button class="btn" onclick="exportLeads()">Export CSV</button>
    <button class="btn" onclick="openSettingsModal()">Settings</button>
    <button class="btn" onclick="openDbInitModal()">Init DB</button>
    <button class="btn" style="color:var(--red)" onclick="openDbResetModal()">Reset DB</button>
  </div>
</header>
<div class="layout">
  <div class="sidebar">
    <div class="sidebar-section">
      <div class="sidebar-title">Stats</div>
      <div class="stat-row"><span>Leads</span><span class="stat-val" id="stat-leads">-</span></div>
      <div class="stat-row"><span>URLs visited</span><span class="stat-val" id="stat-visited">-</span></div>
      <div class="stat-row"><span>Sessions</span><span class="stat-val" id="stat-sessions">-</span></div>
      <div class="stat-row"><span>Runs</span><span class="stat-val" id="stat-runs">-</span></div>
    </div>
    <div class="sidebar-section"><div class="sidebar-title">Sessions</div></div>
    <div class="session-list" id="session-list"></div>
    <div class="sidebar-actions">
      <button class="btn" onclick="newSession()">+ New</button>
      <button class="btn" onclick="loadStats()">Refresh</button>
    </div>
  </div>
  <div class="chat-container">
    <div class="messages" id="messages"></div>
    <div class="input-area">
      <div class="cmd-hint" id="cmd-hint">
        <span class="cmd-pill" onclick="insertCmd('/scrape ')">/scrape</span>
        <span class="cmd-pill" onclick="insertCmd('/results')">/results</span>
        <span class="cmd-pill" onclick="insertCmd('/sessions')">/sessions</span>
        <span class="cmd-pill" onclick="insertCmd('/new ')">/new</span>
        <span class="cmd-pill" onclick="insertCmd('/config')">/config</span>
        <span class="cmd-pill" onclick="insertCmd('/recall ')">/recall</span>
        <span class="cmd-pill" onclick="insertCmd('/clear')">/clear</span>
        <span class="cmd-pill" onclick="insertCmd('/history')">/history</span>
        <span class="cmd-pill" onclick="insertCmd('/help')">/help</span>
      </div>
      <div class="input-row">
        <textarea id="msg-input" rows="1" placeholder="Chat freely, or /scrape to collect leads..."
          onkeydown="handleKey(event)" oninput="autoResize(this)"></textarea>
        <button id="send-btn" onclick="sendMessage()">Send</button>
      </div>
    </div>
  </div>
</div>
<div class="drawer-overlay" id="drawer-overlay" onclick="closeLeadsDrawer()"></div>
<div class="drawer" id="leads-drawer">
  <div class="drawer-header">
    <h2>Leads</h2>
    <input class="drawer-search" id="leads-search" placeholder="Search company, email, category..."
      oninput="debounceLeadSearch()" />
    <button class="btn" onclick="closeLeadsDrawer()">x</button>
  </div>
  <div class="leads-table-wrap">
    <table>
      <thead><tr>
        <th>Company</th><th>First</th><th>Last</th><th>Title</th>
        <th>Email</th><th>Phone</th><th>Category</th>
        <th>Country</th><th>City</th><th>Conf</th><th>Status</th><th></th>
      </tr></thead>
      <tbody id="leads-tbody"></tbody>
    </table>
  </div>
  <div class="drawer-footer">
    <span id="leads-total-label">- leads</span>
    <div class="pagination" id="leads-pagination"></div>
    <button class="btn" onclick="exportLeads()">Export CSV</button>
  </div>
</div>
<div class="modal-overlay" id="settings-modal" onclick="closeSettingsModal()">
  <div class="modal" onclick="event.stopPropagation()">
    <h3>Settings</h3>
    <div class="form-group">
      <label>Default keywords (one per line)</label>
      <textarea id="cfg-keywords" rows="4"></textarea>
    </div>
    <div class="form-row">
      <div class="form-group">
        <label>Max pages / keyword</label>
        <input type="number" id="cfg-max-pages" min="1" max="20" />
      </div>
      <div class="form-group">
        <label>Target new leads (0=unlimited)</label>
        <input type="number" id="cfg-target" min="0" />
      </div>
    </div>
    <div class="form-row">
      <div class="form-group">
        <label>Request delay (s)</label>
        <input type="number" id="cfg-delay" step="0.5" min="0.5" />
      </div>
      <div class="form-group">
        <label>AI confidence threshold</label>
        <input type="number" id="cfg-conf" step="0.05" min="0" max="1" />
      </div>
    </div>
    <div class="form-group">
      <label><input type="checkbox" id="cfg-ai" /> Enable AI enrichment</label>
    </div>
    <div class="modal-actions">
      <button class="btn" onclick="closeSettingsModal()">Cancel</button>
      <button class="btn primary" onclick="saveSettings()">Save</button>
    </div>
  </div>
</div>
<div class="modal-overlay" id="db-init-modal">
  <div class="modal" onclick="event.stopPropagation()">
    <h3>Initialise Database</h3>
    <p style="font-size:.875rem;color:var(--text2)">Creates all required tables. Safe to run multiple times (idempotent). Run once after first deployment.</p>
    <div class="modal-actions">
      <button class="btn" onclick="document.getElementById('db-init-modal').classList.remove('open')">Cancel</button>
      <button class="btn primary" onclick="runDbInit()">Run Init</button>
    </div>
  </div>
</div>
<div class="modal-overlay" id="db-reset-modal">
  <div class="modal" onclick="event.stopPropagation()">
    <h3 style="color:var(--red)">⚠ Reset Database</h3>
    <p style="font-size:.875rem;color:var(--text2)">Drops ALL tables and recreates the schema. Every lead, session and run will be permanently deleted.</p>
    <div class="modal-actions">
      <button class="btn" onclick="document.getElementById('db-reset-modal').classList.remove('open')">Cancel</button>
      <button class="btn" style="background:var(--red);border-color:var(--red);color:#fff" onclick="runDbReset()">Wipe &amp; Reset</button>
    </div>
  </div>
</div>
<div class="modal-overlay" id="session-modal" onclick="closeSessionModal()">
  <div class="modal" onclick="event.stopPropagation()">
    <h3>Sessions</h3>
    <div id="session-modal-list" style="max-height:260px;overflow-y:auto;margin-bottom:.5rem"></div>
    <div class="form-group">
      <label>New session name (leave blank for auto)</label>
      <input type="text" id="new-session-name" placeholder="e.g. UK Packaging Feb 2026" />
    </div>
    <div class="modal-actions">
      <button class="btn" onclick="closeSessionModal()">Close</button>
      <button class="btn primary" onclick="createNewSession()">Create</button>
    </div>
  </div>
</div>
<script>
let currentSessionId=null,isStreaming=false,leadsPage=1,leadsSearch='',leadsTotal=0,searchDebounce=null;
(async()=>{await loadStats();await loadSessions();await resolveSession();await loadSettings();scrollToBottom();})();
async function resolveSession(){const r=await fetch('/api/sessions');const s=await r.json();if(s.length>0){await setSession(s[0].id,s[0].name);}else{const cr=await fetch('/api/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const cs=await cr.json();await setSession(cs.id,cs.name);}}
async function setSession(id,name,skipHistory){currentSessionId=id;document.getElementById('session-badge').textContent='#'+id+' '+name;document.querySelectorAll('.session-item').forEach(el=>el.classList.toggle('active',parseInt(el.dataset.id)===id));if(!skipHistory)await loadHistory(id);}
async function loadStats(){try{const r=await fetch('/api/stats');const s=await r.json();document.getElementById('stat-leads').textContent=s.leads??'-';document.getElementById('stat-visited').textContent=s.visited_urls??'-';document.getElementById('stat-sessions').textContent=s.sessions??'-';document.getElementById('stat-runs').textContent=s.runs??'-';document.getElementById('leads-count-badge').textContent=s.leads??'-';}catch(e){}}
async function loadSessions(){try{const r=await fetch('/api/sessions');const sessions=await r.json();const list=document.getElementById('session-list');list.innerHTML='';sessions.forEach(s=>{const el=document.createElement('div');el.className='session-item'+(s.id===currentSessionId?' active':'');el.dataset.id=s.id;el.title=s.name;el.textContent='#'+s.id+' '+s.name;el.onclick=()=>setSession(s.id,s.name);list.appendChild(el);});}catch(e){}}
async function loadHistory(sessionId){try{const r=await fetch('/api/sessions/'+sessionId+'/history?limit=40');const turns=await r.json();const msgs=document.getElementById('messages');msgs.innerHTML='';turns.forEach(t=>appendMessage(t.role,t.content,t.mode));scrollToBottom();}catch(e){}}
async function loadSettings(){try{const r=await fetch('/api/config');const s=await r.json();document.getElementById('cfg-keywords').value=(s.keywords||[]).join('\\n');document.getElementById('cfg-max-pages').value=s.max_pages??3;document.getElementById('cfg-target').value=s.target_new_leads??0;document.getElementById('cfg-delay').value=s.request_delay_seconds??1.5;document.getElementById('cfg-conf').value=s.ai_confidence_threshold??0;document.getElementById('cfg-ai').checked=!!s.ai_enrichment_enabled;}catch(e){}}
function openSettingsModal(){loadSettings();document.getElementById('settings-modal').classList.add('open');}
function closeSettingsModal(){document.getElementById('settings-modal').classList.remove('open');}
async function saveSettings(){const keywords=document.getElementById('cfg-keywords').value.split('\\n').map(k=>k.trim()).filter(Boolean);const body={keywords,max_pages:parseInt(document.getElementById('cfg-max-pages').value)||3,target_new_leads:parseInt(document.getElementById('cfg-target').value)||0,request_delay_seconds:parseFloat(document.getElementById('cfg-delay').value)||1.5,ai_confidence_threshold:parseFloat(document.getElementById('cfg-conf').value)||0,ai_enrichment_enabled:document.getElementById('cfg-ai').checked};try{const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(r.ok){appendSystem('Settings saved.');closeSettingsModal();}else{appendSystem('Failed: '+await r.text());}}catch(e){appendSystem('Error: '+e);}}
function openDbInitModal(){document.getElementById('db-init-modal').classList.add('open');}
async function runDbInit(){document.getElementById('db-init-modal').classList.remove('open');appendSystem('Initialising database schema...');try{const r=await fetch('/api/db/init',{method:'POST'});const d=await r.json();appendSystem(d.message||'Done.');await loadStats();}catch(e){appendSystem('Error: '+e);}}
function openDbResetModal(){document.getElementById('db-reset-modal').classList.add('open');}
async function runDbReset(){document.getElementById('db-reset-modal').classList.remove('open');appendSystem('Resetting database — dropping all tables...');try{const r=await fetch('/api/db/reset',{method:'POST'});const d=await r.json();appendSystem(d.message||'Done.');await loadStats();}catch(e){appendSystem('Error: '+e);}}
async function openSessionModal(){const r=await fetch('/api/sessions');const sessions=await r.json();const list=document.getElementById('session-modal-list');list.innerHTML='';sessions.forEach(s=>{const row=document.createElement('div');row.style.cssText='display:flex;align-items:center;gap:.5rem;padding:.35rem .5rem;border-radius:6px;cursor:pointer';row.innerHTML='<span style="flex:1;font-size:.82rem">#'+s.id+' '+esc(s.name)+' <small style="color:#666">'+(s.updated_at||'').substring(0,10)+' - '+s.turn_count+' turns</small></span>';row.onclick=()=>{setSession(s.id,s.name);closeSessionModal();};row.onmouseenter=()=>row.style.background='var(--bg3)';row.onmouseleave=()=>row.style.background='';list.appendChild(row);});document.getElementById('session-modal').classList.add('open');}
function closeSessionModal(){document.getElementById('session-modal').classList.remove('open');}
async function createNewSession(){const name=document.getElementById('new-session-name').value.trim()||undefined;const body=name?JSON.stringify({name}):'{}';const r=await fetch('/api/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body});const sess=await r.json();await setSession(sess.id,sess.name);await loadSessions();closeSessionModal();appendSystem('New session #'+sess.id+' "'+sess.name+'" started.');}
async function newSession(){const r=await fetch('/api/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const sess=await r.json();await setSession(sess.id,sess.name);await loadSessions();appendSystem('New session #'+sess.id+' "'+sess.name+'" started.');}
function openLeadsDrawer(){document.getElementById('leads-drawer').classList.add('open');document.getElementById('drawer-overlay').classList.add('open');loadLeads();}
function closeLeadsDrawer(){document.getElementById('leads-drawer').classList.remove('open');document.getElementById('drawer-overlay').classList.remove('open');}
async function loadLeads(page){if(page)leadsPage=page;const q=new URLSearchParams({page:leadsPage,page_size:50,search:leadsSearch});const r=await fetch('/api/leads?'+q);const data=await r.json();leadsTotal=data.total;document.getElementById('leads-total-label').textContent=data.total+' leads';const tbody=document.getElementById('leads-tbody');tbody.innerHTML='';(data.leads||[]).forEach(lead=>{const conf=lead.confidence||0;const badge=conf>=0.7?'conf-high':conf>=0.4?'conf-mid':'conf-low';const tr=document.createElement('tr');tr.innerHTML=\'<td title="\'+esc(lead.company_name)+\'">\'+esc(lead.company_name||\'-\')+\'</td><td>\'+esc(lead.first_name||\'-\')+\'</td><td>\'+esc(lead.last_name||\'-\')+\'</td><td>\'+esc(lead.title||\'-\')+\'</td><td title="\'+esc(lead.email)+\'">\'+esc(lead.email||\'-\')+\'</td><td>\'+esc(lead.phone||\'-\')+\'</td><td>\'+esc(lead.category||\'-\')+\'</td><td>\'+esc(lead.country||\'-\')+\'</td><td>\'+esc(lead.city||\'-\')+\'</td><td><span class="conf-badge \'+badge+\'">\'+conf.toFixed(2)+\'</span></td><td>\'+esc(lead.status||\'New\')+\'</td><td><button class="btn" onclick="archiveLead(\'+lead.id+\',\'+(!lead.archived)+\')">\'+((lead.archived)?\'Restore\':\'Archive\')+\'</button></td>\';tbody.appendChild(tr);});renderPagination(data.total,data.page_size);}
function renderPagination(total,pageSize){const pages=Math.ceil(total/pageSize);const pg=document.getElementById('leads-pagination');pg.innerHTML='';for(let i=1;i<=Math.min(pages,10);i++){const btn=document.createElement('button');btn.className='page-btn'+(i===leadsPage?' active':'');btn.textContent=i;btn.onclick=()=>loadLeads(i);pg.appendChild(btn);}}
function debounceLeadSearch(){clearTimeout(searchDebounce);searchDebounce=setTimeout(()=>{leadsSearch=document.getElementById('leads-search').value;leadsPage=1;loadLeads();},300);}
async function archiveLead(id,archived){await fetch('/api/leads/'+id+'/archive?archived='+archived,{method:'PATCH'});loadLeads();loadStats();}
async function exportLeads(){window.open('/api/leads/export','_blank');}
function appendMessage(role,content,mode){const msgs=document.getElementById('messages');const wrap=document.createElement('div');wrap.className='msg '+role+(mode==='scrape'?' scrape':'');const avatar=document.createElement('div');avatar.className='msg-avatar';avatar.textContent=role==='user'?'U':'B';const bubble=document.createElement('div');bubble.className='msg-bubble';bubble.textContent=content;wrap.appendChild(avatar);wrap.appendChild(bubble);msgs.appendChild(wrap);scrollToBottom();return bubble;}
function appendSystem(text){const msgs=document.getElementById('messages');const el=document.createElement('div');el.style.cssText='text-align:center;color:var(--text3);font-size:.78rem;padding:.25rem';el.textContent=text;msgs.appendChild(el);scrollToBottom();}
function scrollToBottom(){const msgs=document.getElementById('messages');msgs.scrollTop=msgs.scrollHeight;}
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function insertCmd(cmd){const input=document.getElementById('msg-input');input.value=cmd;input.focus();autoResize(input);}
function autoResize(el){el.style.height='auto';el.style.height=Math.min(el.scrollHeight,120)+'px';}
function handleKey(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMessage();}}
async function sendMessage(){const input=document.getElementById('msg-input');const raw=input.value.trim();if(!raw||isStreaming)return;input.value='';autoResize(input);document.getElementById('send-btn').disabled=true;isStreaming=true;const low=raw.toLowerCase();try{
if(low==='/clear'){document.getElementById('messages').innerHTML='';appendSystem('Conversation cleared (DB history preserved).');return;}
if(low==='/help'){appendMessage('assistant','Commands:\\n  /scrape [kw1, kw2, ...]  Run the scraper\\n  /results [N]             Show N most recent leads\\n  /config                  Open settings\\n  /sessions                List sessions\\n  /new [name]              Start a fresh session\\n  /load <id>               Load a previous session\\n  /name <name>             Rename current session\\n  /recall <query>          Search conversation history\\n  /clear                   Clear conversation (DB preserved)\\n  /history                 Show recent turns\\n\\nTip: chat freely with LeadBot for keyword strategy, analysis, etc.');return;}
if(low==='/sessions'){openSessionModal();return;}
if(low.startsWith('/new')){const name=raw.slice(4).trim()||undefined;const body=name?JSON.stringify({name}):'{}';const r=await fetch('/api/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body});const sess=await r.json();await setSession(sess.id,sess.name);await loadSessions();appendSystem('New session #'+sess.id+' "'+sess.name+'".');return;}
if(low.startsWith('/load')){const id=parseInt(raw.split(' ')[1]);if(isNaN(id)){appendSystem('Usage: /load <session_id>');return;}const rH=await fetch('/api/sessions/'+id+'/history?limit=40');if(!rH.ok){appendSystem('Session #'+id+' not found.');return;}const turns=await rH.json();const rS=await fetch('/api/sessions');const sessions=await rS.json();const s=sessions.find(x=>x.id===id);await setSession(id,s?s.name:'#'+id,true);document.getElementById('messages').innerHTML='';turns.forEach(t=>appendMessage(t.role,t.content,t.mode));scrollToBottom();appendSystem('Loaded session #'+id+' ('+turns.length+' turns).');return;}
if(low.startsWith('/name')){const name=raw.slice(5).trim();if(!name){appendSystem('Usage: /name <new name>');return;}await fetch('/api/sessions/'+currentSessionId+'/rename',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});document.getElementById('session-badge').textContent='#'+currentSessionId+' '+name;await loadSessions();appendSystem('Session renamed to "'+name+'".');return;}
if(low==='/config'){openSettingsModal();return;}
if(low.startsWith('/results')){const n=parseInt(raw.split(' ')[1])||10;await showResults(n);return;}
if(low==='/history'){const r=await fetch('/api/sessions/'+currentSessionId+'/history?limit=20');const turns=await r.json();if(!turns.length){appendSystem('No history yet.');return;}let text='Recent turns:\\n';turns.forEach(t=>{const tag=t.mode!=='chat'?' ['+t.mode+']':'';const preview=(t.content||'').substring(0,100)+((t.content||'').length>100?'...':'');text+=t.role+tag+': '+preview+'\\n';});appendMessage('assistant',text.trim());return;}
if(low.startsWith('/recall')){const query=raw.slice(7).trim();if(!query){appendSystem('Usage: /recall <keyword>');return;}await streamChat('Search my conversation history for: "'+query+'". Summarise relevant turns.');return;}
if(low.startsWith('/scrape')){const kwRaw=raw.slice(7).trim();let keywords;if(kwRaw){keywords=kwRaw.split(',').map(k=>k.trim()).filter(Boolean);if(!keywords.length)keywords=[kwRaw];}else{const cfg=await(await fetch('/api/config')).json();keywords=cfg.keywords||['sustainable packaging suppliers UK'];}await streamScrape(keywords);return;}
if(low.startsWith('/')){appendSystem('Unknown command "'+raw.split(' ')[0]+'". Type /help for the command list.');return;}
await streamChat(raw);
}finally{isStreaming=false;document.getElementById('send-btn').disabled=false;}}
async function streamChat(message){appendMessage('user',message,'chat');const bubble=appendMessage('assistant','','chat');bubble.innerHTML='<span class="cursor"></span>';let fullText='';try{const resp=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message,session_id:currentSessionId})});const reader=resp.body.getReader();const dec=new TextDecoder();let buf='';while(true){const{done,value}=await reader.read();if(done)break;buf+=dec.decode(value,{stream:true});const lines=buf.split('\\n');buf=lines.pop();for(const line of lines){if(!line.startsWith('data:'))continue;try{const ev=JSON.parse(line.slice(5).trim());if(ev.type==='token'){fullText+=ev.content;bubble.textContent=fullText;bubble.innerHTML+=('<span class="cursor"></span>');scrollToBottom();}else if(ev.type==='done'){if(ev.session_id)currentSessionId=ev.session_id;bubble.textContent=fullText;loadStats();}else if(ev.type==='error'){bubble.textContent='Error: '+ev.content;}}catch(e){}}}}catch(e){bubble.textContent='Network error: '+e;}finally{bubble.innerHTML=bubble.innerHTML.replace('<span class="cursor"></span>','');}}
async function streamScrape(keywords){appendMessage('user','/scrape '+keywords.join(', '),'scrape');const msgs=document.getElementById('messages');const progressWrap=document.createElement('div');progressWrap.className='msg assistant scrape';const avatar=document.createElement('div');avatar.className='msg-avatar';avatar.textContent='B';const bubble=document.createElement('div');bubble.className='msg-bubble';bubble.style.maxWidth='85%';bubble.innerHTML='<div class="progress-line">Starting scrape...</div>';progressWrap.appendChild(avatar);progressWrap.appendChild(bubble);msgs.appendChild(progressWrap);scrollToBottom();const cfg=await(await fetch('/api/config')).json();let leadsFound=0,progressEl=bubble.querySelector('.progress-line');try{const resp=await fetch('/api/scrape',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({keywords,session_id:currentSessionId,max_pages:cfg.max_pages,target_new_leads:cfg.target_new_leads})});const reader=resp.body.getReader();const dec=new TextDecoder();let buf='';while(true){const{done,value}=await reader.read();if(done)break;buf+=dec.decode(value,{stream:true});const lines=buf.split('\\n');buf=lines.pop();for(const line of lines){if(!line.startsWith('data:'))continue;try{const ev=JSON.parse(line.slice(5).trim());if(ev.type==='progress'){if(!progressEl){progressEl=document.createElement('div');progressEl.className='progress-line';bubble.appendChild(progressEl);}progressEl.textContent=ev.msg;scrollToBottom();}else if(ev.type==='lead'){leadsFound++;progressEl=null;const card=document.createElement('div');card.className='lead-card';const conf=ev.confidence||0;const badge=conf>=0.7?'conf-high':conf>=0.4?'conf-mid':'conf-low';card.innerHTML='<strong>'+esc(ev.company_name||'?')+' <span class="conf-badge '+badge+'">'+conf.toFixed(2)+'</span></strong><div class="meta">'+(ev.email?'Email: '+esc(ev.email)+'  ':'')+(ev.phone?'Tel: '+esc(ev.phone)+'  ':'')+(ev.country?'Country: '+esc(ev.country):'')+(ev.city?' / '+esc(ev.city):'')+(ev.category?'  — '+esc(ev.category):'')+'</div>';bubble.appendChild(card);scrollToBottom();}else if(ev.type==='done'){const summary=document.createElement('div');summary.style.cssText='margin-top:.5rem;font-size:.8rem;color:var(--text2);border-top:1px solid var(--border);padding-top:.4rem';summary.textContent='Done: '+ev.leads_new+' new  '+ev.leads_duplicate+' dup  '+ev.leads_discarded+' discarded  '+ev.pages_visited+' pages';bubble.appendChild(summary);scrollToBottom();loadStats();}else if(ev.type==='error'){const err=document.createElement('div');err.style.cssText='color:var(--red);font-size:.8rem;margin-top:.3rem';err.textContent='Error: '+ev.content;bubble.appendChild(err);}else if(ev.type==='warning'){const w=document.createElement('div');w.style.cssText='color:var(--yellow);font-size:.75rem;margin-top:.2rem';w.textContent='\u26a0 '+ev.msg;bubble.appendChild(w);}}catch(e){}}}}catch(e){bubble.innerHTML+='<div style="color:var(--red);font-size:.8rem">Network error: '+e+'</div>';}if(leadsFound>0)setTimeout(()=>openLeadsDrawer(),500);}
async function showResults(n){try{const r=await fetch('/api/leads?page=1&page_size='+n);const data=await r.json();if(!data.leads||!data.leads.length){appendMessage('assistant','No leads found yet. Run /scrape first.');return;}let text='Showing '+data.leads.length+' of '+data.total+' total leads:\\n\\n';data.leads.forEach(l=>{const email=l.email||'-';const name=(l.company_name||'?').substring(0,30);const conf=(l.confidence||0).toFixed(2);text+='  - '+name+'  '+email+'  '+(l.country||'?')+'  conf='+conf+'\\n';});appendMessage('assistant',text.trim());}catch(e){appendMessage('assistant','Error loading leads: '+e);}}
</script>
</body>
</html>
"""
