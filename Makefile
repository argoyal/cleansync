.PHONY: dev dev-backend dev-frontend dev-seed templates test lint format build fe-install fe-build e2e

# Keep this path short: rclone's bisync lock file name contains both folder paths.
DEV_ROOT := $(CURDIR)/drivesync-data
DEV_ENV := DRIVESYNC_ROOT=$(DEV_ROOT) PUBLIC_URL=http://localhost:8080 \
	ADMIN_PASSWORD=dev-password-change-me FRONTEND_DIR=$(CURDIR)/frontend/dist

dev: templates
	$(MAKE) -j2 dev-backend dev-frontend

dev-backend:
	cd backend && $(DEV_ENV) uv run uvicorn app.main:create_app --factory --reload --port 8080

dev-frontend:
	cd frontend && npm run dev

dev-seed:
	cd backend && $(DEV_ENV) DRIVESYNC_ALLOW_LOCAL_ACCOUNTS=1 uv run python -m app.devtools.seed_local_account alice@example.com --sample-files

templates:
	cd backend && uv run python scripts/make_office_templates.py app/resources/office

test:
	cd backend && uv run pytest -q
	cd frontend && npm test

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && npm run lint && npm run typecheck

format:
	cd backend && uv run ruff check --fix . && uv run ruff format .

build:
	docker build -t drivesync:dev .

fe-install:
	cd frontend && npm ci

fe-build:
	cd frontend && npm run build

e2e:
	cd frontend && E2E_BASE_URL=http://localhost:8080 E2E_ADMIN_PASSWORD=dev-password-change-me npx playwright test
