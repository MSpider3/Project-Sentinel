.PHONY: install install-dev run dev run-dev preview test test-cov lint format lock upgrade clean help

# ── Default ────────────────────────────────────────────────────────
.DEFAULT_GOAL := help

help:
	@echo ""
	@echo "  Project Sentinel — TUI Control Interface"
	@echo "  ==========================================="
	@echo ""
	@echo "  Setup:"
	@echo "    make install      Install production dependencies (uv sync)"
	@echo "    make install-dev  Install + dev dependencies (pytest, ruff, textual-dev)"
	@echo ""
	@echo "  Run:"
	@echo "    make run          Run TUI using production socket"
	@echo "    make run-dev      Run TUI with test socket + debug mode"
	@echo "    make dev          Run with Textual dev tools (CSS live-reload, DOM inspector)"
	@echo "    make preview      Launch standalone OpenCV camera preview window"
	@echo ""
	@echo "  Quality:"
	@echo "    make test         Run all tests"
	@echo "    make test-cov     Run tests with coverage report"
	@echo "    make lint         Lint with ruff"
	@echo "    make format       Format with ruff"
	@echo ""
	@echo "  Dependencies:"
	@echo "    make lock         Regenerate uv.lock from pyproject.toml"
	@echo "    make upgrade      Upgrade all deps and regenerate lock file"
	@echo ""
	@echo "  Misc:"
	@echo "    make clean        Remove all build/cache artifacts"
	@echo ""

# ── Setup ──────────────────────────────────────────────────────────
install:
	uv sync

install-dev:
	uv sync --extra dev

# ── Run ────────────────────────────────────────────────────────────
run:
	uv run sentinel-tui

dev:
	uv run textual run --dev sentinel_tui.app:SentinelApp

# Dev mode: test socket + debug flag
run-dev:
	uv run sentinel-tui --socket /tmp/sentinel_test.sock --debug

# ── Camera Preview ─────────────────────────────────────────────────
preview:
	uv run python sentinel-tui/scripts/camera_preview.py

preview-dev:
	uv run python sentinel-tui/scripts/camera_preview.py --socket /tmp/sentinel_test.sock

# ── Quality ────────────────────────────────────────────────────────
test:
	uv run pytest

test-cov:
	uv run pytest --cov=sentinel_tui --cov-report=term-missing

lint:
	uv run ruff check .

format:
	uv run ruff format .

# ── Lock File Management ───────────────────────────────────────────
lock:
	uv lock

upgrade:
	uv lock --upgrade
	@echo ""
	@echo "  Review changes in uv.lock before committing!"
	@echo ""

# ── Integration Test Helpers ───────────────────────────────────────
# Start daemon with test socket (run in a separate terminal)
daemon-dev:
	@echo "Starting daemon on test socket..."
	SENTINEL_SOCKET_PATH=/tmp/sentinel_test.sock sudo ./venv/bin/python3 core/sentinel_service.py

# ── Cleanup ────────────────────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache
	@echo "Clean complete."
