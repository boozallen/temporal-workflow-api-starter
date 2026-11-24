# Temporal Workflow API Starter - Development Commands
# Usage: just <command> [args]

# Show all available recipes
default:
    @just --list

# ============================================================================
# Setup & Dependencies
# ============================================================================

# Sync the virtual environment with the lockfile (dev + test groups)
sync:
    uv sync --group dev --group test

# Set up the local development environment (dependencies + .env + lockfile)
setup: sync
    @if [ ! -f .env ]; then \
        cp env.template .env; \
        echo "Created .env from env.template - review and customize it"; \
    fi
    uv lock
    @echo "Development environment ready"

# Add a package to the project
add-package package:
    uv add "{{package}}"
    uv lock

# Add a development package to the project
add-dev-package package:
    uv add --dev "{{package}}"
    uv lock

# ============================================================================
# Code Quality
# ============================================================================

# Run ruff lint + the bandit command-injection sink set
lint:
    uv run ruff check src/ tests/
    uv run bandit -c pyproject.toml -r src -t B602,B603,B605,B607

# Auto-fix ruff lint findings
lint-fix:
    uv run ruff check src/ tests/ --fix

# Check code formatting
format:
    uv run ruff format --check src/ tests/

# Auto-format code
format-fix:
    uv run ruff format src/ tests/

# Run basedpyright type checking
type-check:
    uv run basedpyright src/

# Run every quality gate: lint, format, type-check, test
check: lint format type-check test
    @echo "All quality checks passed"

# ============================================================================
# Testing
# ============================================================================
#
# The unit/integration split is by DIRECTORY, not by marker: `tests/unit/` is the
# unit tier and `tests/integration/` is the integration tier, so a test's tier is
# decided by where its file lives and cannot be forgotten. `test` runs both tiers,
# `test-unit` runs `tests/unit/` only, `test-integration` runs `tests/integration/`
# only. No recipe passes `-m`; markers stay declared as cross-cutting trait tags
# but nothing selects on them.
#
# Coverage is always on via `[tool.pytest.ini_options] addopts` in pyproject.toml
# (terminal + HTML into `htmlcov/`), never from a recipe, so there is no separate
# coverage recipe.

# Run all tests (unit + integration)
test:
    uv run pytest tests/

# Run unit tests only
test-unit:
    uv run pytest tests/unit/

# Coverage is disabled for this isolated slice (`--no-cov --cov-fail-under=0`): on
# its own it does not exercise enough of the source tree to meet the configured
# threshold, and instrumenting it would overwrite `htmlcov/` with integration-only
# data. pytest exit code 5 (no tests collected) is treated as success so the recipe
# stays valid if this tier is ever empty.
# Run integration tests only
test-integration:
    #!/usr/bin/env bash
    set -uo pipefail
    uv run pytest tests/integration/ --no-cov --cov-fail-under=0
    status=$?
    if [ "$status" -eq 5 ]; then
        echo "No integration tests collected - not a failure."
        exit 0
    fi
    exit $status

# ============================================================================
# Local Runtime (repo-specific)
# ============================================================================

# Run the API locally with autoreload (no Docker)
dev:
    uv run uvicorn src.main:app --host 127.0.0.1 --port 8000 --reload

# Run the API locally (alias for dev)
run: dev

# Start the API container (assumes a Temporal server is running externally)
up:
    @echo "Temporal server must already be reachable on localhost:7233"
    docker compose up -d api
    @echo "API started! Use 'just logs' to view output"

# Start the API container (alias for up)
start: up

# Stop the API container
down:
    docker compose down

# Stop the API container (alias for down)
stop: down

# View logs (optionally for a single service)
logs service="":
    @if [ -z "{{service}}" ]; then \
        docker compose logs -f; \
    else \
        docker compose logs -f {{service}}; \
    fi

# Build the API Docker image
build:
    docker build -t temporal-workflow-api .

# Check service status and health
status:
    @echo "=== Service Status ==="
    docker compose ps
    @echo ""
    @echo "=== Service Health ==="
    @curl -s http://localhost:8000/health && echo "" || echo "API not ready"

# Stop containers, clean artifacts, and re-run setup
reset: down clean setup
    @echo "Development environment reset complete"

# ============================================================================
# Maintenance
# ============================================================================

# Clean build artifacts and tooling caches
clean:
    rm -rf build/
    rm -rf dist/
    rm -rf htmlcov/
    rm -rf *.egg-info/
    rm -f .coverage
    find . -path ./.venv -prune -o -type d \( -name .pytest_cache -o -name .ruff_cache -o -name __pycache__ \) -prune -exec rm -rf {} +
    find . -path ./.venv -prune -o -type f -name "*.pyc" -exec rm -f {} +
    @echo "Cleaned build artifacts"

# Clean build artifacts plus this project's Docker containers and volumes
clean-all: clean
    docker compose down -v --remove-orphans
    @echo "Cleaned build artifacts and Docker resources"
