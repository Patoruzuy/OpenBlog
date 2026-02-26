.PHONY: up down build test test-integration lint format shell logs migrate

# ─── Docker ───────────────────────────────────────────────────────────────────

up:
	docker compose up --build -d

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f

# ─── Tests ────────────────────────────────────────────────────────────────────

## Unit suite — no external services required
test:
	pytest tests/ -v --ignore=tests/integration

## Integration suite — requires `make up` first
test-integration:
	@echo "Running integration tests — requires services running (make up first)"
	pytest -m integration -v

# ─── Code Quality ─────────────────────────────────────────────────────────────

lint:
	ruff check backend/ tests/

format:
	ruff format backend/ tests/

# ─── Dev Utils ────────────────────────────────────────────────────────────────

shell:
	docker compose exec web flask --app "backend.app:create_app()" shell

# ─── Migrations ───────────────────────────────────────────────────────────────

migrate:
	@echo "Running Alembic migrations..."
	poetry run alembic upgrade head
