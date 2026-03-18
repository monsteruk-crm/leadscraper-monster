# 005 — Semantic scrape-history resume

**Status:** Accepted

## Context
The scraper already persisted exact per-keyword result-page cursors in `search_progress`, but operators often rephrase searches between runs (word order changes, minor typos, synonyms). Exact matching forced these near-identical queries to restart from page 1, wasting crawl budget and duplicating work.

## Decision
Add a semantic query-history layer backed by PostgreSQL `pg_trgm`:

- Introduce `semantic_search_history` with canonicalized `query_text`, `next_page`, run counters, and timestamps.
- Add a trigram GIN index on `query_text` for fast similarity lookup.
- On scrape, resolve resume depth by attempting exact lookup first, then semantic lookup with a configurable similarity threshold (default `0.32`).
- Persist updated depth into both exact (`search_progress`) and semantic (`semantic_search_history`) stores after each keyword search step.
- Expose retrieval APIs so clients can inspect matches and resolve the best resume target before running deeper crawls.

## Consequences
Positive:
- Rephrased or slightly misspelled queries can continue from historical crawl depth.
- Less repeated crawling and faster discovery of deeper result pages.
- APIs now expose explicit search-history retrieval for external tooling and UIs.

Negative:
- Requires `pg_trgm` extension availability in the target PostgreSQL environment.
- Similarity-based resume can produce occasional false-positive matches if thresholds are set too low.
- Adds one more state table to maintain during resets and schema evolution.
