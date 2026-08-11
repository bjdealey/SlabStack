.PHONY: help install dev api ui test lint check schema migrate clean

VENV := backend/.venv
PY   := $(VENV)/bin/python

help:
	@echo "SlabStack"
	@echo ""
	@echo "  make install   Install backend and frontend dependencies"
	@echo "  make api       Run the API on 127.0.0.1:8000"
	@echo "  make ui        Run the UI on 127.0.0.1:5173"
	@echo "  make test      Backend tests"
	@echo "  make lint      Backend lint + frontend typecheck"
	@echo "  make check     Everything CI would run"
	@echo "  make schema    Regenerate docs/schema.sql from the models"
	@echo "  make migrate   Apply Alembic migrations"

install:
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -q --upgrade pip
	$(VENV)/bin/pip install -q -r backend/requirements-dev.txt
	cd frontend && npm install

api:
	cd backend && ../$(VENV)/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

ui:
	cd frontend && npm run dev

test:
	cd backend && ../$(VENV)/bin/python -m pytest -q

lint:
	cd backend && ../$(VENV)/bin/ruff check .
	cd frontend && npm run typecheck

check: test lint
	cd frontend && npm run build

schema:
	cd backend && ../$(VENV)/bin/python -m scripts.dump_schema

migrate:
	cd backend && ../$(VENV)/bin/alembic upgrade head

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf backend/.pytest_cache backend/.ruff_cache frontend/dist
