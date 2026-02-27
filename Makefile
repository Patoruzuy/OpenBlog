.PHONY: up down build test test-integration lint format shell logs migrate \
        i18n-extract i18n-update i18n-compile i18n

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

# ─── i18n ─────────────────────────────────────────────────────────────────────

PYBABEL := pybabel

## Extract translatable strings → messages.pot
i18n-extract:
	$(PYBABEL) extract -F babel.cfg -k lazy_gettext -k _l \
	    -o backend/translations/messages.pot .

## Update all locale .po files from the current .pot
i18n-update: i18n-extract
	$(PYBABEL) update -i backend/translations/messages.pot \
	    -d backend/translations

## Compile .po → .mo (run after editing translations)
i18n-compile:
	$(PYBABEL) compile -d backend/translations

## Full round-trip: extract → update → compile
i18n: i18n-update i18n-compile
