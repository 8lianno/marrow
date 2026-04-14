.PHONY: install test lint typecheck format all clean smoke

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

smoke:
	marrow run tests/fixtures/books/synthetic.pdf --mode api --force
	marrow run tests/fixtures/books/synthetic.pdf --mode host --force

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
