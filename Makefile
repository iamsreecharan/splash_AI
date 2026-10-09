COMPOSE ?= docker compose

.PHONY: init up cloud-up down logs smoke test services
init:
	@test -f .env || cp .env.example .env
	@mkdir -p .secrets
	@chmod 700 .secrets
	@chmod 600 .env

up: init
	SPLASH_RUNTIME_UID=$$(id -u) SPLASH_RUNTIME_GID=$$(id -g) $(COMPOSE) up --build -d --wait

cloud-up: init
	bash infra/cloud-compose.sh up --build -d --wait

services: init
	$(COMPOSE) up -d --wait postgres redis

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs --tail=100 -f backend worker scheduler frontend

smoke:
	python3 scripts/smoke.py

test:
	cd backend && .venv/bin/python -m pytest
	cd frontend && npm run typecheck && npm test && npm run build
