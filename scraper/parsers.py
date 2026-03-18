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

# Email domains that indicate generic/no-reply addresses — deprioritised
_NOREPLY_PREFIXES = ("noreply", "no-reply", "donotreply", "mailer", "info@",
                     "hello@", "contact@", "support@", "admin@", "enquiries@")

_COUNTRY_ALIASES = {
    "uk": "United Kingdom",
    "gb": "United Kingdom",
    "gbr": "United Kingdom",
    "england": "United Kingdom",
    "scotland": "United Kingdom",
    "wales": "United Kingdom",
    "northern ireland": "United Kingdom",
    "us": "United States",
    "usa": "United States",
    "united states of america": "United States",
    "uae": "United Arab Emirates",
}


def parse_lead_info(html: str, source_url: str) -> Optional[Lead]:
    """Extract a Lead from a raw HTML page.

    Scans `mailto:` / `tel:` links BEFORE stripping noise elements because
    contact details are very commonly placed in footers and headers.

    Returns None if the page does not look like a company website.
    """
    try:
        soup = BeautifulSoup(html, "lxml")

        # ── Step 1: harvest contact links from the full DOM ───────────────────
        email = _extract_email_from_links(soup)
        phone = _extract_phone_from_links(soup)
        first_name, last_name = _extract_name_from_schema(soup)
        city, country = _extract_location_from_html(soup)

        # ── Step 2: strip noise; get plain text ───────────────────────────────
        _remove_noise(soup)
        text = soup.get_text(separator=" ", strip=True)

        # ── Step 3: text-regex fallbacks ──────────────────────────────────────
        if not email:
            email = _best_email_from_text(text)
        if not phone:
            phone = _first_match(_PHONE_RE, text)

        # ── Step 4: company name (required) ───────────────────────────────────
        company_name = _extract_company_name(soup)
        if not company_name:
            logger.debug("No company name found — skipping %s", source_url)
            return None

        # ── Step 5: contact name fallback ─────────────────────────────────────
        if not (first_name or last_name):
            full = _extract_contact_name_text(soup, text)
            if " " in full:
                parts = full.split(None, 1)
                first_name, last_name = parts[0], parts[1]
            else:
                first_name = full

        contact_name = f"{first_name} {last_name}".strip()

        return Lead(
            company_name=company_name,
            website=_canonical_website(source_url),
            email=email,
            phone=phone,
            source_url=source_url,
            contact_name=contact_name,
            first_name=first_name,
            last_name=last_name,
            city=city,
            country=country,
        )
    except Exception as exc:
        logger.debug("parse_lead_info failed for %s: %s", source_url, exc)
        return None


# ── Contact link extractors ───────────────────────────────────────────────────

def _extract_email_from_links(soup: BeautifulSoup) -> str:
    """Prefer personal emails over generic ones found in mailto: hrefs."""
    candidates: list[str] = []
    for a in soup.find_all("a", href=re.compile(r"^mailto:", re.I)):
        raw = a["href"][7:].split("?")[0].strip().lower()
        if raw and _EMAIL_RE.match(raw) and "example." not in raw:
            candidates.append(raw)
    if not candidates:
        return ""
    # Prefer personal/named emails over generic ones
    personal = [e for e in candidates
                if not any(e.startswith(p) for p in _NOREPLY_PREFIXES)]
    return (personal or candidates)[0]


def _extract_phone_from_links(soup: BeautifulSoup) -> str:
    """Extract phone number from tel: href attributes."""
    for a in soup.find_all("a", href=re.compile(r"^tel:", re.I)):
        raw = a["href"][4:].strip()
        digits = re.sub(r"\D", "", raw)
        if len(digits) >= 7:
            return raw
    return ""


def _extract_location_from_html(soup: BeautifulSoup) -> tuple[str, str]:
    """Return (city, country) from schema.org JSON-LD, microdata, or meta tags."""
    import json as _json

    # 1. JSON-LD — look for Organization or LocalBusiness with address
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = _json.loads(script.string or "")
            # handle @graph arrays
            nodes = data if isinstance(data, list) else [data]
            nodes += data.get("@graph", []) if isinstance(data, dict) else []
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                addr = node.get("address") or {}
                if isinstance(addr, str):
                    continue
                city = _clean_location_value(addr.get("addressLocality", ""))
                raw_country = addr.get("addressCountry", "")
                if isinstance(raw_country, dict):
                    raw_country = raw_country.get("name", "")
                country = _normalize_country(raw_country)
                if city or country:
                    return city.strip(), country.strip()
        except Exception:
            pass

    # 2. Microdata — itemprop="addressLocality" / "addressCountry"
    city_el = soup.find(attrs={"itemprop": "addressLocality"})
    country_el = soup.find(attrs={"itemprop": "addressCountry"})
    city = _clean_location_value(city_el.get_text(strip=True) if city_el else "")
    country = _normalize_country(country_el.get_text(strip=True) if country_el else "")
    if city or country:
        return city, country

    # 3. Meta tags
    city = ""
    country = ""
    for name in ("geo.placename", "geo.position", "geo.region", "geo.country"):
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            raw = tag["content"].strip()
            if name == "geo.placename":
                parsed_city, parsed_country = _split_place_name(raw)
                city = city or parsed_city
                country = country or parsed_country
            elif name == "geo.country":
                country = country or _normalize_country(raw)
            elif name == "geo.region":
                # geo.region is often a country code or country-like region string.
                normalized = _normalize_country(raw)
                if normalized and normalized != raw:
                    country = country or normalized
    if city or country:
        return city, country

    return "", ""


def _clean_location_value(value: object) -> str:
    return str(value).strip() if value else ""


def _normalize_country(value: object) -> str:
    raw = _clean_location_value(value)
    if not raw:
        return ""
    alias = _COUNTRY_ALIASES.get(raw.lower())
    if alias:
        return alias
    if raw.isupper() and len(raw) <= 3:
        return _COUNTRY_ALIASES.get(raw.lower(), raw)
    return raw


def _split_place_name(value: str) -> tuple[str, str]:
    raw = _clean_location_value(value)
    if not raw:
        return "", ""

    parts = [part.strip() for part in re.split(r"[|,;/]", raw) if part.strip()]
    if len(parts) >= 2:
        city = parts[0]
        country = _normalize_country(parts[-1])
        if city != country:
            return city, country

    normalized = _normalize_country(raw)
    if normalized != raw:
        return "", normalized
    return raw, ""


def _extract_name_from_schema(soup: BeautifulSoup) -> tuple[str, str]:
    """Try schema.org/Person markup for structured first/last name."""
    person_el = soup.find(attrs={"itemtype": re.compile(r"schema\.org/Person", re.I)})
    if person_el:
        fn = person_el.find(attrs={"itemprop": "givenName"})
        ln = person_el.find(attrs={"itemprop": "familyName"})
        if fn or ln:
            return (fn.get_text(strip=True) if fn else "",
                    ln.get_text(strip=True) if ln else "")
        name_tag = person_el.find(attrs={"itemprop": "name"})
        if name_tag:
            full = name_tag.get_text(strip=True)
            parts = full.split(None, 1)
            return (parts[0], parts[1] if len(parts) > 1 else "")
    return "", ""


# ── Noise removal + text helpers ──────────────────────────────────────────────

def _remove_noise(soup: BeautifulSoup) -> None:
    """Strip non-content tags that pollute the extracted text."""
    for tag in soup.find_all(["script", "style", "noscript", "nav",
                               "footer", "header", "aside"]):
        tag.decompose()


def _best_email_from_text(text: str) -> str:
    """Find the best email in plain text, preferring personal over generic."""
    all_emails = _EMAIL_RE.findall(text)
    if not all_emails:
        return ""
    personal = [e for e in all_emails
                if not any(e.lower().startswith(p) for p in _NOREPLY_PREFIXES)]
    return (personal or all_emails)[0]


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


def _extract_contact_name_text(soup: BeautifulSoup, text: str) -> str:
    """Fallback: look for a person name near contact-related keywords."""
    for pattern in (
        r"(?:Contact|Director|Manager|CEO|Founder|Owner)[^\n]{0,40}\n(.{0,120})",
        r"(?:Contact|Director|Manager|CEO|Founder|Owner)[^.]{0,60}",
    ):
        m = re.search(pattern, text)
        if m:
            nm = _PERSON_NAME_RE.search(m.group(0))
            if nm:
                return nm.group(1)
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
