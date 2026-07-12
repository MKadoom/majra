COMPOSE = docker compose -f infra/docker-compose.yml

.PHONY: help up down logs build migrate revision test shell psql seed

help:
	@echo "majra — make targets"
	@echo "  up        start the full stack (postgres, redis, backend, worker, beat)"
	@echo "  down      stop the stack"
	@echo "  logs      tail all logs"
	@echo "  build     rebuild images"
	@echo "  migrate   run alembic upgrade head inside the backend container"
	@echo "  revision  create a new migration (M=\"message\")"
	@echo "  test      run the pytest suite inside the backend container"
	@echo "  shell     open a shell in the backend container"
	@echo "  psql      open psql on the postgres container"

up:
	$(COMPOSE) up -d --build
	@echo "waiting for backend..."
	@sleep 4
	$(MAKE) migrate
	@echo "up — dashboard at http://localhost:3000 · API at http://localhost:8000 (docs at /docs)"

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

build:
	$(COMPOSE) build

migrate:
	$(COMPOSE) run --rm backend alembic upgrade head

revision:
	$(COMPOSE) run --rm backend alembic revision --autogenerate -m "$(M)"

test:
	$(COMPOSE) run --rm -e MAJRA_ENVIRONMENT=test backend python -m pytest -q

web:
	cd frontend && npm install && npm run dev

shell:
	$(COMPOSE) exec backend bash

psql:
	$(COMPOSE) exec postgres psql -U majra -d majra
