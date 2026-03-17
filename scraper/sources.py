"""sources.py — Search-engine querying to discover lead candidate URLs.

Backed by the DuckDuckGo HTML endpoint (no API key required).
Swap or extend this module to add other sources (Bing, Google CSE, Yelp, etc.).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_DDG_URL = "https://html.duckduckgo.com/html/"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


async def search_duckduckgo(
    session: aiohttp.ClientSession,
    query: str,
    max_pages: int = 3,
    delay: float = 1.5,
) -> list[str]:
    """Query DuckDuckGo and return a deduplicated list of result URLs.

    Args:
        session:   Shared aiohttp client session.
        query:     Free-text search string.
        max_pages: Maximum number of result pages to fetch.
        delay:     Seconds to wait between paged requests.

    Returns:
        Deduplicated list of result URLs, with DDG/Bing noise filtered out.
    """
    urls: list[str] = []
    # First page: POST with query; subsequent pages: POST with DDG's next-form payload.
    next_payload: Optional[dict] = {"q": query}

    for page in range(max_pages):
        if next_payload is None:
            break
        try:
            async with session.post(
                _DDG_URL,
                data=next_payload,
                headers=_HEADERS,
                allow_redirects=True,
            ) as resp:
                if resp.status != 200:
                    logger.warning("DDG returned HTTP %d on page %d — stopping.", resp.status, page + 1)
                    break
                html = await resp.text()

            soup = BeautifulSoup(html, "lxml")
            page_urls = _extract_result_urls(soup)
            logger.info("DDG page %d — %d URLs found", page + 1, len(page_urls))
            urls.extend(page_urls)

            # DuckDuckGo pagination: locate the hidden "next page" form
            next_payload = _next_page_payload(soup)

        except aiohttp.ClientError as exc:
            logger.error("DDG request failed on page %d: %s", page + 1, exc)
            break

        if page < max_pages - 1 and next_payload:
            await asyncio.sleep(delay)

    return _deduplicate(urls)


# ── Internal helpers ─────────────────────────────────────────────────────────

def _extract_result_urls(soup: BeautifulSoup) -> list[str]:
    """Parse organic result URLs from a DDG HTML results page."""
    urls = []
    for anchor in soup.select(".result__a"):
        href = anchor.get("href", "")
        if href and href.startswith("http") and "duckduckgo.com" not in href:
            urls.append(href)
    return urls


def _next_page_payload(soup: BeautifulSoup) -> Optional[dict]:
    """Return the POST payload for the next DDG page, or None if no next page."""
    nav_form = soup.find("form", {"class": "nav-link"})
    if not nav_form:
        return None
    return {
        inp["name"]: inp.get("value", "")
        for inp in nav_form.find_all("input", {"name": True})
    }


def _deduplicate(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result
