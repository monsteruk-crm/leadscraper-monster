# 007 — Bing search fallback

**Status:** Accepted

## Context
DuckDuckGo HTML results became unreliable in this environment and started
returning challenge pages instead of usable result links. With no Brave key in
the default local setup, the scraper could complete a run with zero candidate
URLs even when the rest of the pipeline was healthy.

## Decision
Add Bing HTML search as a no-key fallback source and make it the first entry in
the default search-source order. Bing result links are decoded back to their
target URLs before page fetching. DuckDuckGo remains supported as a secondary
fallback, followed by Brave and Nominatim.

## Consequences
Positive outcomes:

- Scraping continues to produce candidate URLs when DuckDuckGo is blocked
- Local development no longer depends on a Brave token for basic discovery
- The existing multi-source run loop can use Bing without changing the rest of
  the pipeline

Negative outcomes:

- The scraper now depends on another external search engine HTML layout
- Bing redirect decoding adds a small amount of connector-specific logic
- Search-source ordering matters more because the first healthy source now
  determines how quickly a run produces results
