.PHONY: up down logs build test smoke lint dev seed ui-build clean help env preflight sandbox verify

# GPU boxes automatically get the NVML overlay (orchestrator GPU stats/leases);
# CPU-only boxes fall back to plain docker-compose.yml and come up cleanly.
GPU_COMPOSE := $(shell command -v nvidia-smi >/dev/null 2>&1 && echo "-f docker-compose.gpu.yml")
COMPOSE      = docker compose -f docker-compose.yml $(GPU_COMPOSE)
COMPOSE_DEV  = $(COMPOSE) -f docker-compose.dev.yml

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

env: ## Create .env from .env.example and generate its secrets (idempotent)
	@test -f .env || { cp .env.example .env && echo "created .env from .env.example"; }
	@grep -qE '^SEARXNG_SECRET=[^[:space:]#]' .env || { \
		sed -i '/^SEARXNG_SECRET=/d' .env; \
		echo "SEARXNG_SECRET=$$(openssl rand -hex 32)" >> .env; \
		echo "generated SEARXNG_SECRET into .env"; \
	}
	@grep -qE '^FORGE_SECRET_KEY=[^[:space:]#]' .env || { \
		sed -i '/^FORGE_SECRET_KEY=/d' .env; \
		echo "FORGE_SECRET_KEY=$$(openssl rand -hex 32)" >> .env; \
		echo "generated FORGE_SECRET_KEY into .env"; \
	}

preflight: ## Check host prerequisites (docker, compose v2, GPU runtime, disk, KVM)
	@./scripts/preflight.sh

up: env preflight ## Preflight, then build and start the full stack (gateway on :8080)
	$(COMPOSE) up -d --build
	# Profile-gated images are invisible to plain `up` — build them explicitly
	# so the orchestrator can spawn sessions/engines from them.
	$(COMPOSE) --profile build-only build session-runner
	$(COMPOSE) --profile engines build airllm imagegen
	# Best-effort prefetch of the big engine images so the first model load
	# doesn't spend minutes pulling. FORGE_SKIP_PULL=1 skips it.
	@if [ "$(FORGE_SKIP_PULL)" != "1" ]; then \
		echo "prefetching engine images (FORGE_SKIP_PULL=1 to skip)..."; \
		$(COMPOSE) --profile engines pull llamacpp vllm || true; \
	fi
	# Sweep lane containers left on pre-rebuild images, then prove the stack
	# is actually up: services running, orchestrator healthy, images built.
	./scripts/verify.sh

verify: ## Sweep stale lane containers + verify services/health/images
	./scripts/verify.sh

down: ## Stop the stack (volumes are kept)
	$(COMPOSE) down --remove-orphans

logs: ## Tail logs from all compose-managed services
	$(COMPOSE) logs -f --tail=200

build: ## Build all images, including session-runner and the engine lanes
	$(COMPOSE) build
	docker build -t forge-session-runner ./session-runner
	docker build -t forge-airllm ./engines/airllm
	docker build -t forge-imagegen ./engines/imagegen

sandbox: ## Build + start the smolvm sandbox lane (opt-in; requires /dev/kvm)
	@test -e /dev/kvm || echo "WARN: /dev/kvm not found — smolvm microVMs will not start on this host"
	$(COMPOSE) --profile sandbox up -d --build smolvm

test: ## Run orchestrator unit tests + UI typecheck
	cd orchestrator && uv run --extra dev pytest -q
	cd ui && npm run typecheck

lint: ## Ruff for python, tsc for the UI
	cd orchestrator && uv run --extra dev ruff check app tests
	cd ui && npm run typecheck

smoke: env preflight ## End-to-end smoke test against a running host (needs GPU + docker)
	./scripts/smoke.sh

seed: ## Re-seed the model catalog (first boot seeds automatically)
	$(COMPOSE) exec orchestrator python /app/scripts/seed_models.py

dev: env preflight ## Start with dev overrides (orchestrator --reload, UI hot reload on :5173)
	$(COMPOSE) --profile build-only build session-runner
	$(COMPOSE_DEV) up --build

ui-build: ## Build the PWA bundle locally
	cd ui && npm ci && npm run build

clean: ## Stop everything and delete all volumes (DESTROYS models, db, workspaces)
	$(COMPOSE) down -v --remove-orphans
