.DEFAULT_GOAL := help
.PHONY: help up down dev frontend-dev migrate seed test frontend-test frontend-e2e lint frontend-lint fmt create-admin

BACKEND := cd backend && uv run
FRONTEND := cd frontend && npx --yes pnpm@10.24.0

help: ## Show available targets
	@grep -hE '^[a-z]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

.env:
	cp .env.example .env

up: .env ## Start local infra (Postgres, Mailpit)
	docker compose up -d --wait

down: ## Stop local infra (keeps data volumes)
	docker compose down

dev: ## Run backend in dev mode
	$(BACKEND) uvicorn app.main:create_app --factory --reload --no-access-log

frontend-dev: ## Run frontend in dev mode
	$(FRONTEND) dev

migrate: ## Apply Alembic migrations
	$(BACKEND) alembic upgrade head

seed: ## Load synthetic demo data into an empty dev database
	$(BACKEND) python -m app.seed

create-admin: ## Create an admin account: make create-admin EMAIL=admin@example.com
	$(BACKEND) python -m app.cli create-admin --email "$(EMAIL)"

test: ## Run backend and frontend unit tests
	$(BACKEND) pytest --cov --cov-report=term-missing
	$(FRONTEND) test

frontend-test: ## Run frontend unit tests
	$(FRONTEND) test

frontend-e2e: ## Run frontend Playwright tests
	$(FRONTEND) test:e2e

lint: ## Run backend and frontend lint/type checks
	$(BACKEND) ruff check .
	$(BACKEND) ruff format --check .
	$(BACKEND) mypy
	$(FRONTEND) lint
	$(FRONTEND) format:check

frontend-lint: ## Run frontend lint and formatting checks
	$(FRONTEND) lint
	$(FRONTEND) format:check

fmt: ## Format code
	$(BACKEND) ruff check --fix .
	$(BACKEND) ruff format .
	$(FRONTEND) format
