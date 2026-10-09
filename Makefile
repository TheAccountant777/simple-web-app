.PHONY: install fmt lint typecheck test eval check

install:
	uv sync

fmt:
	uv run ruff format . && uv run ruff check --fix .

lint:
	uv run ruff check .

typecheck:
	uv run mypy src

test:
	uv run pytest

eval:
	@echo "Plan 3"

check: lint
	uv run ruff format --check .
	$(MAKE) typecheck
	$(MAKE) test
