# Revi — developer entry points. Everything here is also what CI runs.

SHELL := /bin/bash
.DEFAULT_GOAL := help

WAREHOUSE_PATH ?= data/revi_warehouse.duckdb

.PHONY: help bootstrap warehouse db-up db-down migrate sweep api web dev \
        test reference lint fmt typecheck openapi clean

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-12s %s\n", $$1, $$2}'

bootstrap: ## Install Python workspace + frontend deps
	uv sync --all-packages
	@if [ -f apps/web/package.json ]; then cd apps/web && pnpm install; else echo "apps/web not scaffolded yet (M11)"; fi

warehouse: ## Generate the mock DuckDB warehouse + answer key (deterministic)
	uv run python -m revi_warehouse.generate --out $(WAREHOUSE_PATH)

db-up: ## Start Postgres (docker compose)
	docker compose up -d postgres

db-down: ## Stop Postgres
	docker compose down

migrate: ## Apply Postgres migrations
	uv run alembic -c packages/store-postgres/alembic.ini upgrade head

sweep: ## Run one cohort TTL sweep (drop expired cohort tables; --dry-run supported)
	uv run python -m revi_scheduler.sweep

api: ## Run the FastAPI app (dev)
	uv run uvicorn revi_api.main:app --reload --port 8000

web: ## Run the Next.js app (dev)
	cd apps/web && pnpm dev

dev: ## Run API + web together (dev)
	$(MAKE) -j2 api web

test: ## Run the Python test suite (excludes live_llm/postgres markers)
	uv run pytest

reference: ## Reference-conversation + answer-key regression tests (needs `make warehouse`)
	uv run pytest -m reference

lint: ## Ruff + import-linter + naming guard
	uv run ruff check .
	uv run lint-imports
	@! grep -ri --include='*.py' --include='*.ts' --include='*.tsx' --include='*.yaml' --include='*.yml' \
	    --include='*.md' --include='*.toml' --exclude-dir=node_modules --exclude-dir=.venv \
	    --exclude=Makefile -e 'rafi' . \
	    || (echo 'ERROR: forbidden legacy name found (see lines above)'; exit 1)

fmt: ## Auto-format
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## mypy (strict on kernel/domain)
	uv run mypy packages/kernel/src

openapi: ## Export OpenAPI spec to contracts/openapi.json (frontend types: see scripts/generate_web_types.md)
	uv run python -m revi_api.export_openapi

clean: ## Remove generated artifacts
	rm -rf data/*.duckdb data/answer_key.json
