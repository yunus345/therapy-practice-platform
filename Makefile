.DEFAULT_GOAL := help
.PHONY: help up down dev migrate seed create-admin test lint fmt api-types \
	backend-test backend-lint \
	frontend-install frontend-dev frontend-test frontend-e2e frontend-lint

BACKEND := cd backend && uv run
# pnpm comes from Corepack at the version in frontend/package.json ("packageManager").
# CI installs pnpm directly and runs `make <target> PNPM=pnpm`.
PNPM ?= corepack pnpm
FRONTEND := cd frontend && $(PNPM)

help: ## Show available targets
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

.env:
	cp .env.example .env

up: .env ## Start local infra (Postgres, Mailpit)
	docker compose up -d --wait

down: ## Stop local infra (keeps data volumes)
	docker compose down

dev: ## Run backend in dev mode
	$(BACKEND) uvicorn app.main:create_app --factory --reload --no-access-log

migrate: ## Apply Alembic migrations
	$(BACKEND) alembic upgrade head

seed: ## Load synthetic demo data into an empty dev database
	$(BACKEND) python -m app.seed

create-admin: ## Create an admin account: make create-admin EMAIL=admin@example.com
	$(BACKEND) python -m app.cli create-admin --email "$(EMAIL)"

test: backend-test frontend-test ## Run backend and frontend tests

lint: backend-lint frontend-lint ## Run backend and frontend lint and type checks

fmt: ## Format code
	$(BACKEND) ruff check --fix .
	$(BACKEND) ruff format .
	$(FRONTEND) format

api-types: ## Regenerate frontend/openapi.json and the frontend API types from the backend
	$(BACKEND) python -m app.openapi ../frontend/openapi.json
	$(FRONTEND) api-types

backend-test: ## Run backend tests against the test database
	$(BACKEND) pytest --cov --cov-report=term-missing

backend-lint: ## Run ruff and mypy
	$(BACKEND) ruff check .
	$(BACKEND) ruff format --check .
	$(BACKEND) mypy

frontend-install: ## Install frontend dependencies from the lockfile
	$(FRONTEND) install --frozen-lockfile

frontend-dev: ## Run frontend in dev mode
	$(FRONTEND) dev

frontend-test: ## Run frontend unit tests
	$(FRONTEND) test

frontend-e2e: ## Run frontend Playwright tests
	$(FRONTEND) test:e2e

frontend-lint: ## Run frontend lint, formatting and type checks
	$(FRONTEND) lint
	$(FRONTEND) format:check
	$(FRONTEND) typecheck
