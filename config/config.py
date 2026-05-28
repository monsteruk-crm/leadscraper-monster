"""config.py — Runtime settings for the online LeadScraper.

All values are read from environment variables (set via Vercel project
settings or .env for local development).  No local file paths.
"""

from __future__ import annotations

import os


def _get_int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_float_env(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _get_bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_list_env(name: str, default: list[str]) -> list[str]:
    value = os.environ.get(name, "")
    if not value.strip():
        return default
    return [part.strip() for part in value.split(",") if part.strip()]

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
SEARCH_SOURCES: list[str] = _get_list_env(
    "SEARCH_SOURCES",
    ["bing", "duckduckgo", "brave", "nominatim"],
)
BRAVE_SEARCH_API_KEY: str = os.environ.get("BRAVE_SEARCH_API_KEY", "")
BRAVE_RESULTS_PER_PAGE: int = _get_int_env("BRAVE_RESULTS_PER_PAGE", 20)
NOMINATIM_BASE_URL: str = os.environ.get(
    "NOMINATIM_BASE_URL",
    "https://nominatim.openstreetmap.org/search",
)
NOMINATIM_LIMIT: int = _get_int_env("NOMINATIM_LIMIT", 10)
ENABLE_NOMINATIM: bool = _get_bool_env("ENABLE_NOMINATIM", True)

# ── HTTP ─────────────────────────────────────────────────────────────────────
REQUEST_DELAY_SECONDS: float = _get_float_env("REQUEST_DELAY_SECONDS", 1.5)
RESPECT_ROBOTS_TXT: bool = _get_bool_env("RESPECT_ROBOTS_TXT", True)
REQUEST_TIMEOUT_SECONDS: int = 15

# ── AI Enrichment ─────────────────────────────────────────────────────────────
AI_ENRICHMENT_ENABLED: bool = True
AI_CONFIDENCE_THRESHOLD: float = 0.3

# ── Search goals ──────────────────────────────────────────────────────────────
TARGET_NEW_LEADS: int = 0

# ── Dashboard / Leads UI ─────────────────────────────────────────────────────
LEADS_PAGE_SIZE: int = _get_int_env("LEADS_PAGE_SIZE", 25)

# ── Database ─────────────────────────────────────────────────────────────────
DATABASE_URL: str = os.environ.get("DATABASE_URL", "")
