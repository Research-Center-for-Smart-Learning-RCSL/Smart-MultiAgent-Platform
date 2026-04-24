# SMAP build orchestration. Thin wrappers — real work lives in backend/ and frontend/.
# Conventions: every target is idempotent; no target mixes backend and frontend in one command body.

SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

COMPOSE_DIR := deploy/compose
COMPOSE := docker compose -f $(COMPOSE_DIR)/docker-compose.yml

.PHONY: help
help: ## List targets.
	@awk 'BEGIN{FS=":.*##"} /^[a-zA-Z0-9_.-]+:.*##/ {printf "  \033[36m%-22s\033[0m %s\n",$$1,$$2}' $(MAKEFILE_LIST)

# ---------- Formatting ----------
.PHONY: fmt fmt-backend fmt-frontend
fmt: fmt-backend fmt-frontend ## Format all code.
fmt-backend:
	cd backend && ruff format . && ruff check --fix .
fmt-frontend:
	cd frontend && pnpm run fmt

# ---------- Linting ----------
.PHONY: lint lint-backend lint-frontend
lint: lint-backend lint-frontend ## Lint all code.
lint-backend:
	cd backend && ruff check . && lint-imports
lint-frontend:
	cd frontend && pnpm run lint

# ---------- Type checking ----------
.PHONY: typecheck typecheck-backend typecheck-frontend
typecheck: typecheck-backend typecheck-frontend ## Type-check all code.
typecheck-backend:
	cd backend && mypy .
typecheck-frontend:
	cd frontend && pnpm run typecheck

# ---------- Tests ----------
.PHONY: test test-backend test-frontend
test: test-backend test-frontend ## Run all unit tests.
test-backend:
	cd backend && pytest -q
test-frontend:
	cd frontend && pnpm run test

# ---------- Dev servers ----------
.PHONY: dev
dev: ## Run backend + frontend dev servers (foreground, Ctrl-C to stop both).
	@echo "Use two terminals: 'make dev-backend' and 'make dev-frontend'."

.PHONY: dev-backend
dev-backend:
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

.PHONY: dev-frontend
dev-frontend:
	cd frontend && pnpm run dev

# ---------- Docker compose ----------
.PHONY: docker-up docker-down docker-logs
docker-up: ## Bring up the full §25 service stack.
	$(COMPOSE) up -d
docker-down: ## Tear down stack (keeps volumes).
	$(COMPOSE) down
docker-logs:
	$(COMPOSE) logs -f --tail=200

COMPOSE_PROD := docker compose -f $(COMPOSE_DIR)/docker-compose.yml -f $(COMPOSE_DIR)/docker-compose.prod.yml
COMPOSE_TEST := docker compose -f $(COMPOSE_DIR)/docker-compose.yml -f $(COMPOSE_DIR)/compose.test.yml

.PHONY: docker-up-prod docker-up-test docker-down-test
docker-up-prod: ## Bring up production stack (resource limits, replicas).
	$(COMPOSE_PROD) up -d
docker-up-test: ## Bring up E2E test stack (Vault dev, seeded fixtures).
	$(COMPOSE_TEST) up -d --build
docker-down-test: ## Tear down E2E test stack.
	$(COMPOSE_TEST) down -v

# ---------- Bootstrap (Phase B) ----------
.PHONY: bootstrap bootstrap-vault bootstrap-db migrations-check
bootstrap: ## Run Vault/DB/MinIO/Neo4j bootstrap.
	cd backend && python -m smap.bootstrap all
bootstrap-vault:
	cd backend && python -m smap.bootstrap vault-init && python -m smap.bootstrap vault-approle
bootstrap-db:
	cd backend && python -m smap.bootstrap db-init
migrations-check: ## Up → down → up round-trip against the scratch DB.
	cd backend && python -m alembic upgrade head \
	  && python -m alembic downgrade base \
	  && python -m alembic upgrade head

# ---------- OpenAPI codegen (frontend uses generated types) ----------
.PHONY: openapi-types
openapi-types: ## Regenerate frontend API types from backend OpenAPI.
	cd backend && python -m scripts.export_openapi > ../frontend/openapi.json
	cd frontend && pnpm run gen:api

# ---------- Install ----------
.PHONY: install install-backend install-frontend
install: install-backend install-frontend ## Install all deps.
install-backend:
	cd backend && python -m pip install -e '.[dev]'
install-frontend:
	pnpm install
