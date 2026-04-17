.PHONY: install test lint typecheck format all clean

install:
	uv venv
	uv pip install -e ".[dev]"

test:
	pytest -q

lint:
	ruff check .
	ruff format --check .

format:
	ruff format .
	ruff check . --fix

typecheck:
	mypy src/marrow

all: lint typecheck test

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
