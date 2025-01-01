PYTHON ?= python

.PHONY: install format format-check lint typecheck test smoke check clean

install:
	$(PYTHON) -m pip install -e ".[dev]"

format:
	$(PYTHON) -m ruff format .

format-check:
	$(PYTHON) -m ruff format --check .

lint:
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy src

test:
	$(PYTHON) -m pytest

smoke:
	$(PYTHON) scripts/smoke.py

check: format-check lint typecheck test smoke

clean:
	rm -rf build dist .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
