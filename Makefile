# Revi — developer entry points. Everything here is also what CI runs.

SHELL := /bin/bash
.DEFAULT_GOAL := help

WAREHOUSE_PATH ?= data/revi_warehouse.duckdb

.PHONY: help bootstrap warehouse warehouse-diff db-up db-down migrate sweep api web dev \
        test reference lint fmt typecheck openapi token clean

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-12s %s\n", $$1, $$2}'

bootstrap: ## Install Python workspace + frontend deps
	uv sync --all-packages
	@if [ -f apps/web/package.json ]; then cd apps/web && pnpm install; else echo "apps/web not scaffolded yet (M11)"; fi

warehouse: ## Generate the mock DuckDB warehouse + answer key (deterministic)
	uv run python -m revi_warehouse.generate --out $(WAREHOUSE_PATH)

warehouse-diff: ## FN-17: recompute every published finding value by an independent SQL path
	@# Reads ONLY metric contract YAML, the catalog, and each answer's own
	@# published context; emits plain SQL against the warehouse and diffs it
	@# against what the product published. Exits non-zero on any divergence.
	@# Needs `make warehouse` and the stored corpus (docker compose Postgres).
	@# Pass flags through ARGS, e.g. ARGS="--limit 50 --json /tmp/diff.json".
	uv run python -m revi_warehouse_diff $(ARGS)

db-up: ## Start Postgres (docker compose)
	docker compose up -d postgres

db-down: ## Stop Postgres
	docker compose down

migrate: ## Apply Postgres migrations
	uv run alembic -c packages/store-postgres/alembic.ini upgrade head

sweep: ## Run one cohort TTL sweep (drop expired cohort tables; --dry-run supported)
	uv run python -m revi_scheduler.sweep

api: ## Run the FastAPI app (dev; open auth — see REVI_AUTH_DEV_TENANT)
	REVI_AUTH_DEV_TENANT=$${REVI_AUTH_DEV_TENANT:-demo} \
	  uv run uvicorn revi_api.main:app --reload --port 8000

token: ## Mint an API bearer token (needs REVI_AUTH_SECRET; --new-secret to create one)
	uv run python -m revi_api.mint_token $(ARGS)

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
