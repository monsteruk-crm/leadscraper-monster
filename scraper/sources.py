"""sources.py — Search-source querying to discover lead candidate URLs.

Backed by a mix of free and low-cost sources:
  - DuckDuckGo HTML endpoint (no API key)
  - Brave Search API (official API, requires token)
  - OpenStreetMap Nominatim (geo/business directory, rate-limited)
"""

from __future__ import annotations

import base64
import asyncio
import logging
from dataclasses import dataclass
from urllib.parse import parse_qs
from typing import Any, Optional
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup

import config.config as cfg

logger = logging.getLogger(__name__)

_DDG_URL = "https://html.duckduckgo.com/html/"
_BING_URL = "https://www.bing.com/search"
_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
_BING_HEADERS = {"User-Agent": "Mozilla/5.0"}
_HEADERS = {
    # DuckDuckGo is sensitive to bot-like fingerprints. A plain browser UA
    # works more reliably than the earlier custom Vercel-style identifier.
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}
_SEARCH_SOURCES = ("bing", "duckduckgo", "brave", "nominatim")


@dataclass
class SearchBatch:
    source: str
    urls: list[str]
    next_page: int
    exhausted: bool = False


def available_sources() -> list[str]:
    """Return enabled search sources in configured order."""
    enabled: list[str] = []
    for source in cfg.SEARCH_SOURCES:
        name = source.strip().lower()
        if name not in _SEARCH_SOURCES or name in enabled:
            continue
        if name == "brave" and not cfg.BRAVE_SEARCH_API_KEY:
            logger.info("Skipping Brave source: BRAVE_SEARCH_API_KEY is not configured.")
            continue
        if name == "nominatim" and not cfg.ENABLE_NOMINATIM:
            continue
        enabled.append(name)
    if not enabled:
        enabled.append("duckduckgo")
    return enabled


async def search_source(
    session: aiohttp.ClientSession,
    source: str,
    query: str,
    max_pages: int = 3,
    delay: float = 1.5,
    start_page: int = 0,
) -> SearchBatch:
    """Dispatch to a configured search source."""
    normalized = source.strip().lower()
    if normalized == "duckduckgo":
        return await search_duckduckgo(
            session,
            query,
            max_pages=max_pages,
            delay=delay,
            start_page=start_page,
        )
    if normalized == "bing":
        return await search_bing(
            session,
            query,
            max_pages=max_pages,
            delay=delay,
            start_page=start_page,
        )
    if normalized == "brave":
        return await search_brave(
            session,
            query,
            api_key=cfg.BRAVE_SEARCH_API_KEY,
            max_pages=max_pages,
            delay=delay,
            start_page=start_page,
        )
    if normalized == "nominatim":
        return await search_nominatim(
            session,
            query,
            delay=delay,
            start_page=start_page,
        )
    raise ValueError(f"Unsupported search source: {source}")


async def search_duckduckgo(
    session: aiohttp.ClientSession,
    query: str,
    max_pages: int = 3,
    delay: float = 1.5,
    start_page: int = 0,
) -> SearchBatch:
    """Query DuckDuckGo HTML results."""
    urls: list[str] = []
    page_index = max(0, start_page)
    pages_fetched = 0

    while pages_fetched < max_pages:
        payload = {"q": query, "s": str(page_index * 30)}
        html = ""
        resp_status = 0
        for attempt in range(3):
            try:
                async with session.get(
                    _DDG_URL,
                    params=payload,
                    headers=_HEADERS,
                    allow_redirects=True,
                ) as resp:
                    resp_status = resp.status
                    html = await resp.text()
                if resp_status == 200:
                    break
                logger.warning(
                    "DDG returned HTTP %d on page %d attempt %d; retrying.",
                    resp_status,
                    page_index + 1,
                    attempt + 1,
                )
            except Exception as exc:
                logger.error(
                    "DDG request failed on page %d attempt %d: %s",
                    page_index + 1,
                    attempt + 1,
                    exc,
                )
                resp_status = 0
            if attempt < 2:
                await asyncio.sleep(delay)

        if resp_status != 200:
            return SearchBatch("duckduckgo", _deduplicate(urls), page_index, exhausted=False)

        soup = BeautifulSoup(html, "lxml")
        page_urls = _extract_result_urls(soup)
        logger.info("DDG page %d: %d URLs found", page_index + 1, len(page_urls))
        if not page_urls:
            return SearchBatch("duckduckgo", _deduplicate(urls), page_index, exhausted=True)
        urls.extend(page_urls)
        page_index += 1
        pages_fetched += 1

        if pages_fetched < max_pages:
            await asyncio.sleep(delay)

    return SearchBatch("duckduckgo", _deduplicate(urls), page_index, exhausted=False)


async def search_bing(
    session: aiohttp.ClientSession,
    query: str,
    max_pages: int = 3,
    delay: float = 1.5,
    start_page: int = 0,
) -> SearchBatch:
    """Query Bing HTML results and decode redirected result links."""
    urls: list[str] = []
    page_index = max(0, start_page)
    pages_fetched = 0

    while pages_fetched < max_pages:
        params = {
            "q": query,
            "first": str(page_index * 10 + 1),
            "count": "10",
        }
        try:
            async with session.get(
                _BING_URL,
                params=params,
                headers=_BING_HEADERS,
                allow_redirects=True,
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        "Bing returned HTTP %d on page %d; stopping.",
                        resp.status,
                        page_index + 1,
                    )
                    return SearchBatch("bing", _deduplicate(urls), page_index, exhausted=False)
                html = await resp.text()
        except Exception as exc:
            logger.error("Bing request failed on page %d: %s", page_index + 1, exc)
            return SearchBatch("bing", _deduplicate(urls), page_index, exhausted=False)

        soup = BeautifulSoup(html, "lxml")
        page_urls = _extract_bing_urls(soup)
        logger.info("Bing page %d: %d URLs found", page_index + 1, len(page_urls))
        if not page_urls:
            return SearchBatch("bing", _deduplicate(urls), page_index, exhausted=True)
        urls.extend(page_urls)
        page_index += 1
        pages_fetched += 1

        if pages_fetched < max_pages:
            await asyncio.sleep(delay)

    return SearchBatch("bing", _deduplicate(urls), page_index, exhausted=False)


async def search_brave(
    session: aiohttp.ClientSession,
    query: str,
    api_key: str,
    max_pages: int = 3,
    delay: float = 1.5,
    start_page: int = 0,
) -> SearchBatch:
    """Query the official Brave Search API."""
    if not api_key:
        return SearchBatch("brave", [], start_page, exhausted=True)

    urls: list[str] = []
    page_index = max(0, start_page)
    pages_fetched = 0
    page_size = max(1, min(cfg.BRAVE_RESULTS_PER_PAGE, 20))
    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": api_key,
        "User-Agent": _HEADERS["User-Agent"],
    }

    while pages_fetched < max_pages:
        params = {
            "q": query,
            "count": str(page_size),
            "offset": str(page_index * page_size),
        }
        try:
            async with session.get(_BRAVE_URL, params=params, headers=headers) as resp:
                if resp.status != 200:
                    logger.warning(
                        "Brave returned HTTP %d on page %d; stopping.",
                        resp.status,
                        page_index + 1,
                    )
                    return SearchBatch("brave", _deduplicate(urls), page_index, exhausted=False)
                payload = await resp.json(content_type=None)
        except Exception as exc:
            logger.error("Brave request failed on page %d: %s", page_index + 1, exc)
            return SearchBatch("brave", _deduplicate(urls), page_index, exhausted=False)

        page_urls = _extract_brave_urls(payload)
        logger.info("Brave page %d: %d URLs found", page_index + 1, len(page_urls))
        if not page_urls:
            return SearchBatch("brave", _deduplicate(urls), page_index, exhausted=True)

        urls.extend(page_urls)
        page_index += 1
        pages_fetched += 1

        if pages_fetched < max_pages:
            await asyncio.sleep(delay)

    return SearchBatch("brave", _deduplicate(urls), page_index, exhausted=False)


async def search_nominatim(
    session: aiohttp.ClientSession,
    query: str,
    delay: float = 1.5,
    start_page: int = 0,
) -> SearchBatch:
    """Query Nominatim once and extract business website URLs from tags."""
    if start_page > 0:
        return SearchBatch("nominatim", [], start_page, exhausted=True)

    params = {
        "q": query,
        "format": "jsonv2",
        "limit": str(max(1, min(cfg.NOMINATIM_LIMIT, 50))),
        "extratags": "1",
        "addressdetails": "0",
        "dedupe": "1",
    }
    headers = {
        "Accept": "application/json",
        "User-Agent": _HEADERS["User-Agent"],
    }
    try:
        async with session.get(
            cfg.NOMINATIM_BASE_URL,
            params=params,
            headers=headers,
        ) as resp:
            if resp.status != 200:
                logger.warning("Nominatim returned HTTP %d; stopping.", resp.status)
                return SearchBatch("nominatim", [], 0, exhausted=False)
            payload = await resp.json(content_type=None)
    except Exception as exc:
        logger.error("Nominatim request failed: %s", exc)
        return SearchBatch("nominatim", [], 0, exhausted=False)

    await asyncio.sleep(delay)

    urls = _extract_nominatim_urls(payload)
    logger.info("Nominatim returned %d website URLs", len(urls))
    return SearchBatch("nominatim", urls, 1, exhausted=True)


def _extract_result_urls(soup: BeautifulSoup) -> list[str]:
    urls = []
    for anchor in soup.select(".result__a"):
        href = anchor.get("href", "")
        if href and href.startswith("http") and "duckduckgo.com" not in href:
            urls.append(href)
    return urls


def _extract_bing_urls(soup: BeautifulSoup) -> list[str]:
    urls: list[str] = []
    for anchor in soup.select("li.b_algo h2 a"):
        href = anchor.get("href", "")
        normalized = _normalize_bing_url(href)
        if normalized:
            urls.append(normalized)
    return _deduplicate(urls)


def _extract_brave_urls(payload: dict[str, Any]) -> list[str]:
    results = payload.get("web", {}).get("results", [])
    urls: list[str] = []
    for item in results:
        url = item.get("url")
        if isinstance(url, str) and url.startswith("http"):
            urls.append(url)
    return urls


def _extract_nominatim_urls(payload: Any) -> list[str]:
    urls: list[str] = []
    if not isinstance(payload, list):
        return urls

    for item in payload:
        if not isinstance(item, dict):
            continue
        tags = item.get("extratags")
        if not isinstance(tags, dict):
            continue
        for key in ("website", "contact:website", "url"):
            candidate = tags.get(key)
            normalized = _normalize_http_url(candidate)
            if normalized:
                urls.append(normalized)
                break
    return _deduplicate(urls)


def _normalize_http_url(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    url = value.strip()
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return url


def _normalize_bing_url(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    href = value.strip()
    if not href:
        return None
    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    encoded = query.get("u", [""])[0]
    if encoded.startswith("a1"):
        encoded = encoded[2:]
        encoded += "=" * (-len(encoded) % 4)
        try:
            decoded = base64.b64decode(encoded).decode("utf-8", errors="ignore")
            if decoded.startswith(("http://", "https://")):
                return decoded
        except Exception:
            pass
    if href.startswith(("http://", "https://")) and "bing.com" not in parsed.netloc:
        return href
    return None


def _deduplicate(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result
