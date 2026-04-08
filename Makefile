.PHONY: help install run dry-run

# Configure Python
PYTHON ?= python3
export PYTHONDONTWRITEBYTECODE := 1
export PYTHONUNBUFFERED := 1

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-12s %s\n", $$1, $$2}'

install: ## Install dependencies
	$(PYTHON) -m pip install -r requirements.txt

run: ## Run the rewriter
	$(PYTHON) main.py

dry-run: ## Run in dry-run mode (no changes)
	DRY_RUN=true $(PYTHON) main.py
