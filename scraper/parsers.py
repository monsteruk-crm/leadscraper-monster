"""parsers.py — HTML → Lead extraction using BeautifulSoup + regex.

Each helper function is independent and unit-testable in isolation.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from scraper.models import Lead

logger = logging.getLogger(__name__)

# ── Regex patterns ────────────────────────────────────────────────────────────
_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)
_PHONE_RE = re.compile(
    r"(?:(?:\+|00)\d{1,3}[\s.\-]?)?"
    r"(?:\(?\d{2,4}\)?[\s.\-]?)?"
    r"\d{3,4}[\s.\-]?\d{3,4}"
)
_PERSON_NAME_RE = re.compile(r"\b([A-Z][a-z]+ [A-Z][a-z]+)\b")


def parse_lead_info(html: str, source_url: str) -> Optional[Lead]:
    """Extract a Lead from a raw HTML page.

    Returns None if the page does not look like a company website
    (e.g. social-network profiles, aggregators, plain news articles).

    Args:
        html:       Raw HTML string.
        source_url: The URL the HTML was fetched from.

    Returns:
        A partially- or fully-populated Lead, or None.
    """
    try:
        soup = BeautifulSoup(html, "lxml")
        _remove_noise(soup)
        text = soup.get_text(separator=" ", strip=True)

        company_name = _extract_company_name(soup)
        if not company_name:
            logger.debug("No company name found — skipping %s", source_url)
            return None

        return Lead(
            company_name=company_name,
            website=_canonical_website(source_url),
            email=_first_match(_EMAIL_RE, text),
            phone=_first_match(_PHONE_RE, text),
            source_url=source_url,
            contact_name=_extract_contact_name(soup, text),
        )
    except Exception as exc:
        logger.debug("parse_lead_info failed for %s: %s", source_url, exc)
        return None


# ── Extraction helpers ────────────────────────────────────────────────────────

def _remove_noise(soup: BeautifulSoup) -> None:
    """Strip non-content tags that pollute the extracted text."""
    for tag in soup.find_all(["script", "style", "noscript", "nav", "footer", "header", "aside"]):
        tag.decompose()


def _extract_company_name(soup: BeautifulSoup) -> str:
    """Try og:site_name → og:title → <title> → <h1> in that order."""
    og_site = soup.find("meta", property="og:site_name")
    if og_site and og_site.get("content"):
        return og_site["content"].strip()

    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        return _clean_title(og_title["content"])

    title_tag = soup.find("title")
    if title_tag and title_tag.text:
        return _clean_title(title_tag.text)

    h1 = soup.find("h1")
    if h1 and h1.text:
        return h1.text.strip()[:120]

    return ""


def _extract_contact_name(soup: BeautifulSoup, text: str) -> str:
    """Look for schema.org Person markup; fall back to a name near 'Contact'."""
    person_el = soup.find(attrs={"itemtype": re.compile(r"schema\.org/Person", re.I)})
    if person_el:
        name_tag = person_el.find(attrs={"itemprop": "name"})
        if name_tag:
            return name_tag.get_text(strip=True)

    contact_block = re.search(r"Contact[\w\s]{0,30}\n(.{0,150})", text)
    if contact_block:
        m = _PERSON_NAME_RE.search(contact_block.group(1))
        if m:
            return m.group(1)

    return ""


def _canonical_website(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _clean_title(raw: str) -> str:
    """Strip common site-name suffixes from page titles."""
    for sep in ("|", "–", "-", "•", "·"):
        if sep in raw:
            raw = raw.split(sep)[0]
    return raw.strip()[:120]


def _first_match(pattern: re.Pattern, text: str) -> str:
    m = pattern.search(text)
    return m.group(0) if m else ""
