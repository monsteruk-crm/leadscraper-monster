# LeadScraper Monster — Documentation

B2B lead scraping SaaS deployed on Vercel with a Python/FastAPI API, a new React frontend scaffold, a PostgreSQL backend, and an OpenAI-powered enrichment pipeline.

## Contents

- [Architecture](architecture.md) — pipeline, modules, lead schema, database tables, SSE events
- [Configuration](configuration.md) — environment variables and runtime settings
- [Usage](usage.md) — local development, deployment, API reference, slash commands
- [Dashboard](dashboard.md) — React scaffold status, UI guide, and migration notes

## Architecture Decision Records

- [ADR index](adr/README.md)
- [001 — Vercel + FastAPI deployment](adr/001-vercel-fastapi-deployment.md)
- [002 — PostgreSQL + asyncpg persistence](adr/002-postgres-asyncpg.md)
- [003 — Lead data quality fields](adr/003-lead-data-quality-fields.md)
- [004 — asyncpg connection pool reliability](adr/004-connection-pool-reliability.md)

- [005 — Semantic scrape-history resume](adr/005-semantic-scrape-history-resume.md)
