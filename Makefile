.PHONY: up down logs build test smoke lint dev seed ui-build clean help

COMPOSE      = docker compose
COMPOSE_DEV  = docker compose -f docker-compose.yml -f docker-compose.dev.yml

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

up: ## Build and start the full stack (gateway on :8080)
	$(COMPOSE) up -d --build
	# Profile-gated images are invisible to plain `up` — build them explicitly
	# so the orchestrator can spawn sessions/engines from them.
	$(COMPOSE) --profile build-only build session-runner
	$(COMPOSE) --profile engines build airllm imagegen

down: ## Stop the stack (volumes are kept)
	$(COMPOSE) down --remove-orphans

logs: ## Tail logs from all compose-managed services
	$(COMPOSE) logs -f --tail=200

build: ## Build all images, including session-runner and the engine lanes
	$(COMPOSE) build
	docker build -t forge-session-runner ./session-runner
	docker build -t forge-airllm ./engines/airllm
	docker build -t forge-imagegen ./engines/imagegen

test: ## Run orchestrator unit tests + UI typecheck
	cd orchestrator && uv run --extra dev pytest -q
	cd ui && npm run typecheck

lint: ## Ruff for python, tsc for the UI
	cd orchestrator && uv run --extra dev ruff check app tests
	cd ui && npm run typecheck

smoke: ## End-to-end smoke test against a running host (needs GPU + docker)
	./scripts/smoke.sh

seed: ## Insert the seed model catalog into the orchestrator DB
	$(COMPOSE) exec orchestrator python /app/scripts/seed_models.py

dev: ## Start with dev overrides (orchestrator --reload, UI hot reload on :5173)
	$(COMPOSE) --profile build-only build session-runner
	$(COMPOSE_DEV) up --build

ui-build: ## Build the PWA bundle locally
	cd ui && npm ci && npm run build

clean: ## Stop everything and delete all volumes (DESTROYS models, db, workspaces)
	$(COMPOSE) down -v --remove-orphans
