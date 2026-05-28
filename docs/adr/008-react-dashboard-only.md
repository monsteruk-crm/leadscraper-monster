# 008 — React Dashboard Only

**Status:** Accepted

## Context
The repository previously exposed a second dashboard entrypoint from `main.py` alongside the React dashboard under `frontend/`. That duplicate surface created confusion about the canonical UI, duplicated routing behavior, and left a large amount of dead HTML/JS embedded in the API entrypoint.

## Decision
Keep the React dashboard as the only user-facing frontend.

- `main.py` now exposes the API and redirects `/` to `/dashboard/`
- `frontend/` is the only user-facing dashboard implementation
- Documentation and deployment notes now describe the React dashboard as the canonical UI

## Consequences
Positive:
- One frontend path to maintain and reason about
- No duplicated UI behavior between Python and React
- Smaller, cleaner `main.py` and less stale documentation

Negative:
- Users who bookmarked `/` are now redirected to `/dashboard/`
- The repo no longer contains a fallback Python dashboard for offline use
