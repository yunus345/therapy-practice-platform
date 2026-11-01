.DEFAULT_GOAL := help
.PHONY: help up down dev migrate seed test lint fmt

BACKEND := cd backend && uv run

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

migrate: ## Apply Alembic migrations
	$(BACKEND) alembic upgrade head

seed: ## Load synthetic demo data
	@echo "Seed data is not available yet: it arrives in Milestone 6." >&2
	@exit 1

test: ## Run backend tests
	$(BACKEND) pytest --cov --cov-report=term-missing

lint: ## Run ruff and mypy
	$(BACKEND) ruff check .
	$(BACKEND) ruff format --check .
	$(BACKEND) mypy

fmt: ## Format code
	$(BACKEND) ruff check --fix .
	$(BACKEND) ruff format .
