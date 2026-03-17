"""config.py — Runtime settings for the online LeadScraper.

All values are read from environment variables (set via Vercel project
settings or .env for local development).  No local file paths.
"""

from __future__ import annotations

import os

# ── OpenAI ───────────────────────────────────────────────────────────────────
OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL: str = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# ── Search ───────────────────────────────────────────────────────────────────
SEARCH_KEYWORDS: list[str] = [
    "sustainable packaging suppliers UK",
    "eco packaging manufacturer UK",
    "B2B packaging solutions England",
    "green packaging company UK",
]
MAX_PAGES: int = 3

# ── HTTP ─────────────────────────────────────────────────────────────────────
REQUEST_DELAY_SECONDS: float = 1.5
RESPECT_ROBOTS_TXT: bool = True
REQUEST_TIMEOUT_SECONDS: int = 15

# ── AI Enrichment ─────────────────────────────────────────────────────────────
AI_ENRICHMENT_ENABLED: bool = True
AI_CONFIDENCE_THRESHOLD: float = 0.3

# ── Search goals ──────────────────────────────────────────────────────────────
TARGET_NEW_LEADS: int = 0

# ── Database ─────────────────────────────────────────────────────────────────
DATABASE_URL: str = os.environ.get("DATABASE_URL", "")
