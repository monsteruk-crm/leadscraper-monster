# 001 — Vercel + FastAPI Deployment

**Status:** Accepted

## Context

The original LeadScraper project was a local CLI tool (Python, SQLite, CSV export) that ran only on the developer's machine. The requirement was to make it accessible as a web app without managing servers.

Options considered:
- Self-hosted VPS (e.g. DigitalOcean) with Uvicorn + nginx
- Railway / Render (container-based PaaS)
- Vercel serverless with Python runtime

## Decision

Deploy on **Vercel** using its Python ASGI serverless runtime with **FastAPI**.

Reasons:
- Zero infrastructure management; no servers to maintain.
- Free tier sufficient for low-volume B2B lead scraping.
- FastAPI's async support maps cleanly onto Vercel's ASGI adapter.
- `vercel dev` gives a close-to-production local environment without Docker.
- `vercel.json` with a single `rewrites` rule routes all traffic to `main.py`.

The entire application (API + SPA HTML) lives in `main.py` to keep the deployment footprint minimal — one Python file, one entry-point.

## Consequences

**Positive:**
- Instant deploys via `vercel --prod`.
- No ops burden; SSL, CDN, and scaling handled automatically.
- `public/` directory served as static assets alongside the API.

**Negative:**
- Serverless functions have a cold-start latency (~1–3 s on first request).
- Long-running scrapes can hit Vercel's 60-second function timeout; very large keyword sets may need to be split.
- All state must be stored externally (PostgreSQL) — no in-process SQLite.
