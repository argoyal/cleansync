# Contributing

You do not need a Google account to work on CleanSync: tests use a fake rclone, and `make dev-seed` adds a pretend Google account whose "Drive" is a local folder (it needs rclone installed).

    cd backend && uv sync && cd ../frontend && npm ci
    make test        # backend pytest + frontend vitest
    make lint        # ruff, eslint, tsc
    make dev-seed    # once: alice@example.com backed by drivesync-data/local-remotes/
    make dev         # API on :8080, web app with hot reload on http://localhost:5173
    make e2e         # Playwright against :8080 (after `make fe-build`; see frontend/e2e)

Tests that use a real rclone binary between two local folders are marked `realrclone`:
`cd backend && uv run pytest -m realrclone`. Keep the data folder path short; rclone's bisync names its lock file after both folder paths.

Rules:
- Write the failing test first.
- All rclone arguments are built in `app/services/rclone/commands.py`. Every command that can delete must include `--drive-use-trash=true`.
- Never log tokens, passwords or client secrets.
- No personal domains, emails or paths in code, tests or docs; use `example.com`.
- Frontend dependencies are pinned to exact versions. Talk to the server only through `src/api/`.
- Paths from the browser go through `normalize_rel` and `safe_join`. Never serve a user's file in a way that lets it run script on CleanSync's address (see `app/api/serve.py`).
