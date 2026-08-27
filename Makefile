.PHONY: format lint test test-infra

format:
	uv run ruff check . --fix
	uv run ruff format .
	@sh scripts/check-format-excludes.sh

lint:
	uv run mypy src tests
	uv run basedpyright
	uv run ruff check .
	uv run ruff format --check .
	uv run flake8 src tests

test:
	uv run pytest -m "not infrastructure"

test-infra:
	@uv run pytest -m infrastructure; status=$$?; \
	if [ $$status -eq 5 ]; then echo "Infrastructure-тесты отсутствуют."; exit 0; fi; \
	exit $$status
