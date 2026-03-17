"""scraper.py — LeadScraper orchestrator (online edition).

Adapted for Vercel / PostgreSQL:
  • No CSV writes  (data is saved via the `db` callbacks)
  • No SQLite      (all persistence via injected async callbacks)
  • SSE-friendly   (yields LeadEvent objects for streaming)

Pipeline: search_sites → fetch_page → parse_lead_info → enrich_lead
          → deduplicate → db.insert_lead
"""

from __future__ import annotations

import asyncio
import logging
import urllib.robotparser
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Optional, Set
from urllib.parse import urlparse

import aiohttp
from openai import AsyncOpenAI

import config.config as cfg
from scraper.enricher import enrich_lead as _enrich
from scraper.models import Lead, ScrapeResult
from scraper.parsers import parse_lead_info as _parse
from scraper.sources import search_duckduckgo

logger = logging.getLogger(__name__)

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


@dataclass
class LeadEvent:
    """A single SSE-streamable event from the scraper."""
    type: str   # "lead" | "progress" | "done" | "error"
    data: dict


class LeadScraper:
    """Orchestrates the full lead-scraping pipeline.

    Designed as an async context manager:

        async with LeadScraper() as scraper:
            async for event in scraper.run_streaming(keywords, ...):
                yield event  # push to SSE
    """

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None
        self._openai: Optional[AsyncOpenAI] = None
        self._robots_cache: dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}

    async def __aenter__(self) -> "LeadScraper":
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=cfg.REQUEST_TIMEOUT_SECONDS),
            headers=_HTTP_HEADERS,
        )
        if cfg.AI_ENRICHMENT_ENABLED and cfg.OPENAI_API_KEY:
            self._openai = AsyncOpenAI(api_key=cfg.OPENAI_API_KEY)
        return self

    async def __aexit__(self, *_) -> None:
        if self._session:
            await self._session.close()

    # ── Public step functions ─────────────────────────────────────────────────

    async def search_sites(self, keywords: str) -> list[str]:
        logger.info("Searching: %r", keywords)
        urls = await search_duckduckgo(
            self._session,
            keywords,
            max_pages=cfg.MAX_PAGES,
            delay=cfg.REQUEST_DELAY_SECONDS,
        )
        logger.info("Found %d candidate URLs", len(urls))
        return urls

    async def fetch_page(self, url: str) -> Optional[str]:
        if cfg.RESPECT_ROBOTS_TXT and not await self._robots_allowed(url):
            return None
        await asyncio.sleep(cfg.REQUEST_DELAY_SECONDS)
        try:
            async with self._session.get(url, allow_redirects=True) as resp:
                if resp.status != 200:
                    return None
                if "text/html" not in resp.headers.get("Content-Type", ""):
                    return None
                return await resp.text(errors="replace")
        except Exception as exc:
            logger.debug("fetch_page error for %s: %s", url, exc)
            return None

    async def enrich_lead(self, lead: Lead) -> Lead:
        if not self._openai:
            return lead
        return await _enrich(self._openai, lead, cfg.OPENAI_MODEL)

    # ── Streaming pipeline ────────────────────────────────────────────────────

    async def run_streaming(
        self,
        keywords: list[str],
        visited: Set[str],
        existing_keys: Set[str],
        on_lead: Callable,           # async (lead, session_id) -> None
        on_progress: Callable,       # async (run_id, pages, new_leads) -> None
        target_new_leads: int = 0,
        run_id: Optional[int] = None,
        session_id: Optional[int] = None,
    ) -> AsyncIterator[LeadEvent]:
        """Run the full pipeline and yield LeadEvents.

        Args:
            keywords:         Search keyword strings.
            visited:          Pre-loaded set of visited URLs (mutable, updated here).
            existing_keys:    Pre-loaded set of known dedupe keys.
            on_lead:          Async callback called with (lead, session_id) after save.
            on_progress:      Async callback called with (run_id, pages, new_leads).
            target_new_leads: Stop early once this many new leads are found (0 = off).
            run_id:           DB run record id.
            session_id:       Current session id.
        """
        result = ScrapeResult()
        seen_this_run: set[str] = set()
        target_reached = False

        for kw in keywords:
            if target_reached:
                break

            yield LeadEvent("progress", {"msg": f"Searching: {kw}", "phase": "search"})

            try:
                urls = await self.search_sites(kw)
            except Exception as exc:
                yield LeadEvent("error", {"msg": f"Search failed for '{kw}': {exc}"})
                continue

            yield LeadEvent("progress", {
                "msg": f"Found {len(urls)} candidate URLs for '{kw}'",
                "phase": "fetch",
            })

            for url in urls:
                if target_new_leads > 0 and result.leads_new >= target_new_leads:
                    target_reached = True
                    break

                if url in visited:
                    continue

                html = await self.fetch_page(url)
                visited.add(url)

                if html is None:
                    continue

                result.pages_visited += 1

                lead = _parse(html, url)
                if lead is None:
                    result.leads_discarded += 1
                    continue

                lead = await self.enrich_lead(lead)

                if lead.confidence < cfg.AI_CONFIDENCE_THRESHOLD:
                    result.leads_discarded += 1
                    continue

                key = lead.dedupe_key
                if key in existing_keys or key in seen_this_run:
                    result.leads_duplicate += 1
                    continue

                seen_this_run.add(key)
                existing_keys.add(key)
                result.leads.append(lead)
                result.leads_new += 1

                # Persist via callback
                try:
                    await on_lead(lead, session_id)
                except Exception as exc:
                    logger.warning("on_lead callback failed: %s", exc)

                # Yield SSE event for real-time streaming
                yield LeadEvent("lead", {
                    "company_name": lead.company_name,
                    "email": lead.email or "",
                    "website": lead.website,
                    "category": lead.category,
                    "confidence": lead.confidence,
                    "country": lead.country,
                    "city": lead.city,
                })

                # Update run progress in DB
                if run_id is not None:
                    try:
                        await on_progress(run_id, result.pages_visited, result.leads_new)
                    except Exception:
                        pass

        result.leads = []  # already persisted via on_lead callback

        yield LeadEvent("done", {
            "pages_visited": result.pages_visited,
            "leads_new": result.leads_new,
            "leads_duplicate": result.leads_duplicate,
            "leads_discarded": result.leads_discarded,
        })

    # ── Robots.txt helper ─────────────────────────────────────────────────────

    async def _robots_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if base not in self._robots_cache:
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(f"{base}/robots.txt")
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, rp.read)
                self._robots_cache[base] = rp
            except Exception:
                self._robots_cache[base] = None
        rp = self._robots_cache[base]
        return rp is None or rp.can_fetch("*", url)
