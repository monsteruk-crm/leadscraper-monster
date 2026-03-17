"""models.py — Shared data models for the LeadScraper agent."""

from __future__ import annotations

from dataclasses import dataclass, asdict, field, fields
from typing import List


@dataclass
class Lead:
    """A single scraped lead.

    Field names match the Monster CRM lead schema so the output CSV can
    be imported with no column remapping.
    """

    company_name: str = ""
    website: str = ""
    country: str = ""
    city: str = ""
    first_name: str = ""
    last_name: str = ""
    contact_name: str = ""   # first_name + last_name joined; kept for compat
    title: str = ""          # job title / role
    role: str = ""           # legacy alias for title
    email: str = ""
    phone: str = ""
    source_url: str = ""
    category: str = ""
    size_signals: str = ""
    notes: str = ""
    confidence: float = 0.0
    status: str = "New"
    owner: str = ""
    last_touch: str = ""
    opt_out: bool = False

    # ── De-duplication ───────────────────────────────────────────────────────

    @property
    def dedupe_key(self) -> str:
        """Normalised key used to detect duplicates (email > website > name)."""
        return (self.email or self.website or self.company_name).lower().strip()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def fieldnames(cls) -> list[str]:
        return [f.name for f in fields(cls)]


@dataclass
class ScrapeResult:
    """Outcome of a full scraper run."""

    leads: List[Lead] = field(default_factory=list)   # new leads only
    leads_new: int = 0
    leads_duplicate: int = 0
    pages_visited: int = 0
    leads_discarded: int = 0
