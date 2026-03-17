# Repository Guidelines

## Project Structure & Module Organization
- `main.py` hosts the FastAPI app and the single HTML shell that ships under `/`; keep API logic, schemas, and response helpers here until the project needs more modules.
- `requirements.txt` lists runtime dependencies (currently `fastapi`); update it whenever you add a new PyPI package.
- Public assets live in `public/`; place static files (icons, manifest, extra CSS) here so Vercel can serve them directly.
- Use `README.md` for deployment/vercel notes and link back to this guide when describing how pieces fit together.

## Build, Test, and Development Commands
- `pip install -r requirements.txt`: installs FastAPI (add other libs before committing so the lock stays current).
- `uvicorn main:app --reload`: runs the app locally on port 8000 with auto-reload; ideal for API iterations before Vercel deployment.
- `npm install -g vercel` then `vercel dev`: mirrors the production serverless environment (run from repo root); `vercel --prod` publishes to Vercel when ready.
- `curl http://localhost:8000/api/data`: quick sanity check for sample endpoints after spinning up the dev server.

## Coding Style & Naming Conventions
- Follow PEP 8: 4-space indentation, blank lines between logical blocks, and limiting lines to ~88 characters.
- Prefer explicit type hints for public functions, snake_case for variables/functions, PascalCase for classes.
- Keep inline HTML/JS snippets readable by indenting consistently within the triple-quoted string in `main.py`.
- Format with `black` or your favorite formatter before pushing to keep styles consistent (no formatter is enforced yet, but run one locally).

## Testing Guidelines
- No automated tests exist yet; add `tests/` with `test_*.py` naming once features grow.
- When tests arrive, run them with `pytest tests` and record coverage targets in PR descriptions. For now, rely on manual `curl`/browser checks of `/api/data` and `/`.

## Commit & Pull Request Guidelines
- Commit messages should use the imperative voice (`Add`, `Fix`, `Document`) and reference the primary change (e.g., "Document API sample payloads").
- PRs need a clear summary, linked issue (if one exists), and a short list of how you verified the change (commands/latest responses). Include screenshots only when the UI/HTML changes.
- Tag reviewers when ready and mention any post-merge work (e.g., Vercel re-deploy).

## Deployment & Configuration Tips
- Vercel routes `/` to the FastAPI app automatically; keep server logic inside `main.py` and static assets in `public/` for best alignment with the demo.
- If you add env vars, document them here so future contributors know what Vercel secrets to set before pushing.
