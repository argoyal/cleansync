.PHONY: dev dev-seed test lint format build

dev:
	cd backend && uv run uvicorn app.main:create_app --factory --reload --port 8080

test:
	cd backend && uv run pytest -q

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .

format:
	cd backend && uv run ruff check --fix . && uv run ruff format .

build:
	docker build -t drivesync:dev .
