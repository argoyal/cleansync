.PHONY: dev dev-backend dev-frontend dev-seed fake-model templates test lint format build fe-install fe-build e2e e2e-new

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
	cd backend && $(DEV_ENV) DRIVESYNC_ALLOW_LOCAL_ACCOUNTS=1 uv run python -m app.devtools.seed_local_account alice@example.com --sample-files --sample-mail

# A pretend AI model for trying mail rules without a real one: http://127.0.0.1:11999/v1, model "fake-sorter".
# Development only: like dev-seed, it needs DRIVESYNC_ALLOW_LOCAL_ACCOUNTS=1.
fake-model:
	cd backend && DRIVESYNC_ALLOW_LOCAL_ACCOUNTS=1 uv run python -m app.devtools.fake_model --port 11999

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
	docker build -t cleansync:dev .

fe-install:
	cd frontend && npm ci

fe-build:
	cd frontend && npm run build

# Optional: E2E_CONTAINER=<name of the running container> lets the mail test check the fake Gmail's trash.
# The rules test needs the pretend model where the server can reach it (make fake-model, or E2E_FAKE_MODEL_URL).
e2e:
	cd frontend && E2E_CONTAINER=$(E2E_CONTAINER) E2E_BASE_URL=http://localhost:8080 E2E_ADMIN_PASSWORD=dev-password-change-me npx playwright test

# The New menu's browser tests: an image with New switched on (every release has it off).
e2e-new:
	docker build --build-arg VITE_SHOW_NEW=1 -t cleansync:new .
	cd frontend && E2E_SHOW_NEW=1 E2E_CONTAINER=$(E2E_CONTAINER) E2E_BASE_URL=http://localhost:8080 E2E_ADMIN_PASSWORD=dev-password-change-me npx playwright test
