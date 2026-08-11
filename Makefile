.PHONY: help install dev api ui test lint check parity schema migrate clean

VENV := backend/.venv

# Use the project venv when there is one, and whatever is already on PATH when
# there is not. CI installs into the runner's own environment and a contributor
# may prefer their own tooling; neither should have to create backend/.venv just
# to run `make check`.
VENV_BIN := $(abspath $(VENV)/bin)
BIN      := $(if $(wildcard $(VENV)/bin/python),$(VENV_BIN)/,)
PY       := $(BIN)python
PYTEST   := $(PY) -m pytest
RUFF     := $(BIN)ruff
UVICORN  := $(BIN)uvicorn
ALEMBIC  := $(BIN)alembic

help:
	@echo "SlabStack"
	@echo ""
	@echo "  make install   Install backend and frontend dependencies"
	@echo "  make api       Run the API on 127.0.0.1:8000"
	@echo "  make ui        Run the UI on 127.0.0.1:5173"
	@echo "  make test      Backend tests"
	@echo "  make lint      Backend lint + frontend typecheck"
	@echo "  make parity    Check the demo engine still agrees with the server"
	@echo "  make check     Everything CI runs"
	@echo "  make schema    Regenerate docs/schema.sql from the models"
	@echo "  make migrate   Apply Alembic migrations"

install:
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -q --upgrade pip
	$(VENV)/bin/pip install -q -r backend/requirements-dev.txt
	cd frontend && npm install

api:
	cd backend && $(UVICORN) app.main:app --reload --host 127.0.0.1 --port 8000

ui:
	cd frontend && npm run dev

test:
	cd backend && $(PYTEST) -q

lint:
	cd backend && $(RUFF) check .
	cd frontend && npm run typecheck

# The demo is a hand-maintained port of the backend engines, so it can drift
# without anything failing to compile. This is what notices.
parity:
	cd frontend && ./node_modules/.bin/esbuild --bundle --platform=node --format=esm \
		--alias:@=$(CURDIR)/frontend/src \
		--outfile=$(CURDIR)/tools/parity/.demo.mjs $(CURDIR)/tools/parity/demo.ts >/dev/null
	node tools/parity/.demo.mjs > tools/parity/.demo.json
	$(PY) tools/parity/server.py > tools/parity/.server.json
	$(PY) tools/parity/compare.py

# Kept in step with .github/workflows/ci.yml on purpose: if this passes locally
# and CI disagrees, one of the two is lying.
check: test lint parity
	cd frontend && npm run build
	cd frontend && npm run build:demo

schema:
	cd backend && $(PY) -m scripts.dump_schema

migrate:
	cd backend && $(ALEMBIC) upgrade head

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf backend/.pytest_cache backend/.ruff_cache frontend/dist
