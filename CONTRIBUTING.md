# Contributing

You do not need a Google account to work on DriveSync: tests use a fake rclone.

    cd backend && uv sync
    make test      # unit tests
    make lint      # ruff
    make dev       # run the API with reload on :8080

Tests that use a real rclone binary between two local folders are marked `realrclone`:
`cd backend && uv run pytest -m realrclone`.

Rules:
- Write the failing test first.
- All rclone arguments are built in `app/services/rclone/commands.py`. Every command that can delete must include `--drive-use-trash=true`.
- Never log tokens, passwords or client secrets.
- No personal domains, emails or paths in code, tests or docs; use `example.com`.
