# 005 — Multi-source search connectors

**Status:** Accepted

## Context
The prototype discussion referenced multiple search connectors, but the shipped scraper only queried DuckDuckGo and stored a single resume cursor per keyword. That limited discovery breadth and prevented independent continuation across multiple search sources. We also wanted safer alternatives to direct SERP scraping, especially for low-cost lead discovery that does not depend on aggressive anti-bot evasion.

## Decision
Adopt a multi-source search layer with three connectors:

- DuckDuckGo HTML as the no-key fallback source
- Brave Search API as the preferred official web-search API when a token is configured
- OpenStreetMap Nominatim as a one-shot business/location discovery source that extracts website tags from OSM entries

Persist search progress per `(keyword, source)` pair in PostgreSQL, including both the next cursor and whether that source has been exhausted for the keyword. Record the sources used on each `search_runs` row so run history reflects the connector set for that scrape.

## Consequences
Positive outcomes:

- More lead-discovery coverage without relying on a single search source
- Better operational safety because Brave uses an official API and Nominatim is constrained to a lightweight directory-style lookup
- Repeated searches can resume each connector independently instead of losing progress when multiple sources are enabled

Negative outcomes:

- More moving parts to configure, especially `BRAVE_SEARCH_API_KEY`
- Nominatim is intentionally limited and only yields candidate URLs when OSM data includes website tags
- Existing databases need the search-progress schema upgrade path to run before the new resume model is fully available
