# TaskFlow Pro - common tasks.
# Run `make help` to see everything available.

.DEFAULT_GOAL := help
.PHONY: help setup db backend frontend seed test lint clean

BACKEND := backend
PY := $(BACKEND)/.venv/bin/python
PIP := $(BACKEND)/.venv/bin/pip

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## One-command setup: database, dependencies, tables and seed data
	@echo "==> Starting Postgres"
	docker compose up -d
	@echo "==> Waiting for Postgres to accept connections"
	@until docker exec taskflow-db pg_isready -U taskflow -d taskflow >/dev/null 2>&1; do sleep 1; done
	@echo "==> Creating virtualenv and installing dependencies"
	test -d $(BACKEND)/.venv || python3 -m venv $(BACKEND)/.venv
	$(PIP) install --quiet --upgrade pip
	$(PIP) install --quiet -r $(BACKEND)/requirements-dev.txt
	@test -f .env || (cp .env.example .env && echo "==> Created .env from .env.example")
	@echo "==> Installing frontend dependencies"
	cd frontend && npm install --silent
	@echo "==> Seeding the board"
	cd $(BACKEND) && .venv/bin/python seed.py
	@echo ""
	@echo "Setup complete. Now run these in two terminals:"
	@echo "    make backend     API   -> http://localhost:8000  (docs at /docs)"
	@echo "    make frontend    Board -> http://localhost:5173"

db: ## Start Postgres only
	docker compose up -d

backend: ## Run the API at http://localhost:8000 (docs at /docs)
	@test -d $(BACKEND)/.venv || (echo "Run 'make setup' first." && exit 1)
	cd $(BACKEND) && .venv/bin/uvicorn app.main:app --reload --port 8000

frontend: ## Run the React dev server at http://localhost:5173
	@test -d frontend/node_modules || (echo "==> Installing frontend dependencies first" && cd frontend && npm install --silent)
	cd frontend && npm run dev

seed: ## Reset the board to the demo project
	cd $(BACKEND) && .venv/bin/python seed.py

test: ## Run every test (backend + frontend)
	@echo "==> Backend"
	cd $(BACKEND) && .venv/bin/python -m pytest --cov=app --cov-report=term-missing
	@echo ""
	@echo "==> Frontend"
	@test -d frontend/node_modules || (cd frontend && npm install --silent)
	cd frontend && npm run test

test-backend: ## Backend tests only
	cd $(BACKEND) && .venv/bin/python -m pytest --cov=app --cov-report=term-missing

test-frontend: ## Frontend tests only
	@test -d frontend/node_modules || (cd frontend && npm install --silent)
	cd frontend && npm run test

lint: ## Check formatting and lint rules
	cd $(BACKEND) && .venv/bin/ruff check app tests

clean: ## Stop the database and delete its volume (destroys all data)
	docker compose down -v
