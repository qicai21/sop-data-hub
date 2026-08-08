.PHONY: install install-dev test test-unit test-functional test-live-readonly smoke-runtime clean

PY ?= PYTHONPATH=src .venv/bin/python
PYTEST ?= $(PY) -m pytest

install:
	$(PY) -m pip install -e .

install-dev:
	$(PY) -m pip install -e ".[dev]"

# Portable gate: unit + functional only (live excluded by path).
test:
	$(PYTEST) tests/unit tests/functional -q

test-unit:
	$(PYTEST) tests/unit -q

test-functional:
	$(PYTEST) tests/functional -q

test-live-readonly:
	SOP_TEST_LIVE=1 $(PYTEST) tests/live -q

# Runtime release checks — fail closed when critical deps missing.
smoke-runtime:
	$(PY) scripts/smoke_runtime.py

clean:
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	find . -name ".DS_Store" -delete 2>/dev/null || true
	rm -rf build/ dist/ *.egg-info/
