PYTHON ?= python
VENV_PY = $(if $(filter Windows_NT,$(OS)),.venv/Scripts/python.exe,.venv/bin/python)
.PHONY: setup seed dev test lint
setup:
	$(PYTHON) -m venv .venv
	$(VENV_PY) -m pip install -r requirements.txt
	$(VENV_PY) -m playwright install chromium
	cd frontend && npm install
seed:
	$(VENV_PY) -m backend.app.seed
dev:
	$(VENV_PY) -m scripts.dev
test:
	$(VENV_PY) -m pytest
	cd frontend && npm run test
lint:
	$(VENV_PY) -m ruff check backend tests scripts
	$(VENV_PY) -m mypy backend
	cd frontend && npm run lint && npm run typecheck
