.PHONY: install install-dev run dev run-dev run-debug preview preview-auth preview-enroll deploy test test-cov lint format lock upgrade clean help

# ── Default ────────────────────────────────────────────────────────
.DEFAULT_GOAL := help

help:
	@echo ""
	@echo "  Project Sentinel — TUI Control Interface"
	@echo "  ==========================================="
	@echo ""
	@echo "  Setup:"
	@echo "    make install       Install production dependencies (uv sync)"
	@echo "    make install-dev   Install + dev dependencies (pytest, ruff, textual-dev)"
	@echo "    make deploy        Push updated core/ files to live daemon (no full reinstall)"
	@echo ""
	@echo "  Run:"
	@echo "    make run           Run TUI using production socket"
	@echo "    make run-debug     Run TUI with test socket + debug mode"
	@echo "    make dev           Run with Textual dev tools (CSS live-reload, DOM inspector)"
	@echo "    make preview       Launch standalone OpenCV camera preview window"
	@echo "    make preview-auth  Launch auth frame preview (use during active auth test)"
	@echo "    make preview-enroll Launch enroll frame preview (use during active enroll)"
	@echo ""
	@echo "  Quality:"
	@echo "    make test          Run all tests"
	@echo "    make test-cov      Run tests with coverage"
	@echo "    make lint          Lint with ruff"
	@echo "    make format        Format with ruff"
	@echo ""
	@echo "  Dependencies:"
	@echo "    make lock          Regenerate uv.lock from pyproject.toml"
	@echo "    make upgrade       Upgrade all deps and regenerate lock file"
	@echo ""
	@echo "  Misc:"
	@echo "    make clean         Remove all build/cache artifacts"
	@echo ""

# ── Setup ──────────────────────────────────────────────────────────
install:
	uv sync

install-dev:
	uv sync --extra dev

# ── Run ────────────────────────────────────────────────────────────
run:
	uv run sentinel_tui

dev:
	uv run textual run --dev sentinel_tui.app:SentinelApp

# Dev mode: test socket + debug flag
run-debug:
	uv run sentinel_tui --socket /tmp/sentinel_test.sock --debug

# ── Camera Preview ─────────────────────────────────────────────────
preview:
	uv run python sentinel_tui/scripts/camera_preview.py

preview-debug:
	uv run python sentinel_tui/scripts/camera_preview.py --socket /tmp/sentinel_test.sock

preview-auth:
	uv run python sentinel_tui/scripts/frame_preview.py --mode auth

preview-enroll:
	uv run python sentinel_tui/scripts/frame_preview.py --mode enroll

# ── Deploy (quick update without full reinstall) ────────────────────
# Copies updated core/*.py and sentinel_tui/ to the live system installation.
# Use this during development instead of re-running setup.sh.
deploy:
	@echo "Deploying updated files to live system (requires sudo)..."
	sudo cp core/*.py /usr/lib/project-sentinel/
	@SITE=$$(find /usr/lib/project-sentinel/venv -name site-packages -type d | head -1); \
	 if [ -n "$$SITE" ]; then sudo cp core/*.py "$$SITE/"; echo "  → Core modules deployed to $$SITE"; \
	 else echo "  ⚠ site-packages not found, only root path updated"; fi
	sudo cp -r sentinel_tui/. /usr/lib/project-sentinel/sentinel_tui/
	@echo "  → Syncing gallery files to deployed models dir..."
	@sudo mkdir -p /usr/lib/project-sentinel/models
	@if ls models/gallery_*.npy 1>/dev/null 2>&1; then \
		sudo cp models/gallery_*.npy /usr/lib/project-sentinel/models/; \
		echo "  → Gallery files deployed: $$(ls models/gallery_*.npy | xargs -n1 basename | tr '\\n' ' ')"; \
	else \
		echo "  ⚠ No gallery_*.npy files found in models/ — user may not be enrolled"; \
	fi
	@echo "  → Syncing PAM C source to deployed archive dir..."
	@sudo mkdir -p /usr/lib/project-sentinel/archive
	@sudo cp archive/pam_sentinel.c /usr/lib/project-sentinel/archive/
	@for svc in sentinel-daemon sentinel-backend sentinel; do \
		if systemctl is-active --quiet $$svc 2>/dev/null; then \
			echo "  → Restarting $$svc..."; \
			sudo systemctl restart $$svc; \
			break; \
		fi; \
	done
	@echo "Deploy complete."


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
