.PHONY: setup run test lint typecheck bench db-up db-down reset demo forget eval-smoke

setup:
	uv venv --python 3.12
	uv sync --extra dev

run:
	uv run uvicorn recallweave.api.app:app --reload --port 8100

test:
	uv run pytest

lint:
	uv run ruff check src tests evals bench scripts sdk_demo.py
	uv run ruff format --check src tests evals bench scripts sdk_demo.py

typecheck:
	uv run mypy src/recallweave --ignore-missing-imports

db-up:
	docker compose up -d db
	@echo "Postgres+pgvector on :5432 — set RECALLWEAVE_DB_URL per .env.example"

db-down:
	docker compose down

reset:
	docker compose down -v
	rm -f data/recallweave.db

bench:
	uv run python bench/latency.py

forget:
	uv run python scripts/forget.py

eval-smoke:
	uv run python evals/run_eval.py --dataset evals/fixtures/mini_eval.jsonl --conditions C0,C3 --judge mock
