SHELL := /bin/bash
UV := uv
CP_DIR := services/control-plane
WEB_DIR := apps/web
export NODE_OPTIONS := --no-network-family-autoselection

.PHONY: setup doctor dev dev-api dev-web db-up db-down migrate test test-integration lint typecheck security validate clean

setup: db-up ## Install all dependencies and run migrations
	cd $(CP_DIR) && $(UV) sync --all-extras
	cd $(WEB_DIR) && npm install
	$(MAKE) migrate

doctor: ## Check local environment health
	cd $(CP_DIR) && $(UV) run nexus doctor

db-up: ## Start PostgreSQL via Docker Compose
	docker compose up -d postgres
	@until docker compose exec postgres pg_isready -U nexus -d nexus >/dev/null 2>&1; do sleep 1; done

db-down: ## Stop PostgreSQL
	docker compose down

migrate: ## Apply database migrations
	cd $(CP_DIR) && $(UV) run alembic upgrade head

dev: db-up ## Start control plane API and web dashboard
	@trap 'kill 0' EXIT; \
	( cd $(CP_DIR) && $(UV) run nexus start --foreground ) & \
	( cd $(WEB_DIR) && npm run dev ) & \
	wait

dev-api: db-up ## Start only the control plane API
	cd $(CP_DIR) && $(UV) run nexus start --foreground

dev-web: ## Start only the web dashboard
	cd $(WEB_DIR) && npm run dev

test: ## Run unit tests (no live workers, no network)
	cd $(CP_DIR) && $(UV) run pytest -m "not integration and not live" -q

test-integration: db-up ## Run integration tests (requires PostgreSQL)
	cd $(CP_DIR) && $(UV) run pytest -m integration -q

lint: ## Lint Python and web code
	cd $(CP_DIR) && $(UV) run ruff check src tests && $(UV) run ruff format --check src tests
	cd $(WEB_DIR) && npm run lint

typecheck: ## Type-check Python and TypeScript
	cd $(CP_DIR) && $(UV) run mypy src
	cd $(WEB_DIR) && npx tsc --noEmit

security: ## Dependency and secret checks (free, local)
	cd $(CP_DIR) && $(UV) run pip-audit --skip-editable || true
	cd $(WEB_DIR) && npm audit --audit-level=high || true

build: ## Production build of the web dashboard
	cd $(WEB_DIR) && npm run build

validate: lint typecheck test build ## Full local validation pipeline

clean: ## Remove caches and build artifacts
	rm -rf $(CP_DIR)/.venv $(CP_DIR)/.pytest_cache $(CP_DIR)/.mypy_cache $(CP_DIR)/.ruff_cache
	rm -rf $(WEB_DIR)/node_modules $(WEB_DIR)/.next
