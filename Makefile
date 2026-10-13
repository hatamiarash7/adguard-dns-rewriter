.PHONY: help install install-dev run dry-run test lint check clean

# Configure Python
PYTHON ?= python3
export PYTHONDONTWRITEBYTECODE := 1
export PYTHONUNBUFFERED := 1

# Load KEY=value pairs from .env (if present) so targets work out of the box.
# Keep .env to simple "KEY=value" lines -- one per line, '#' starts a comment.
ifneq (,$(wildcard .env))
include .env
export
endif

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-12s %s\n", $$1, $$2}'

install: ## Install runtime dependencies
	$(PYTHON) -m pip install -r requirements.txt

install-dev: ## Install runtime + dev dependencies (tests, linter)
	$(PYTHON) -m pip install -r requirements.txt -r requirements-dev.txt

run: ## Run the rewriter (loads .env automatically if present)
	$(PYTHON) main.py

dry-run: ## Run in dry-run mode (no changes are made)
	DRY_RUN=true $(PYTHON) main.py

test: ## Run the test suite
	$(PYTHON) -m pytest

lint: ## Run flake8 on main.py and tests
	$(PYTHON) -m flake8 main.py tests

check: lint test ## Run lint + tests

clean: ## Remove caches and test artifacts
	rm -rf .pytest_cache
	find . -name __pycache__ -not -path './.venv/*' -exec rm -rf {} +
