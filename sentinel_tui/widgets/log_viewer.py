"""
log_viewer.py — Stream and filter structured JSON logs from the daemon.

Features:
  - Parses JSON lines (fallback to plain text)
  - Color coding by log level
  - Pause/resume toggle
  - Auto-scroll (tail) toggle
  - Component and level filtering
  - Dev mode: direct file tail; Prod mode: to use `get_logs` RPC (Phase 8)
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Button, Checkbox, RichLog, Select

from sentinel_tui.constants import LOG_FILE, LOG_VIEWER_MAX_LINES

logger = logging.getLogger(__name__)

# Fallback regex for plain-text log lines (if not JSON)
_LOG_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2},\d{3})\s+"
    r"\[(?P<level>[A-Z]+)\]\s+"
    r"(?P<component>[\w\.\-]+)\s+—\s+"
    r"(?P<message>.*)$"
)


class LogViewer(Widget):
    """
    Scrolling, auto-tailing log display panel.
    """

    DEFAULT_CSS = """
    LogViewer {
        height: 1fr;
        min-height: 12;
        layout: vertical;
        border: round #1e3a5f;
        background: #080c15;
    }
    #log-controls {
        height: 3;
        layout: horizontal;
        padding: 0 1;
        border-bottom: solid #1e3a5f;
        background: #0f1729;
        align: left middle;
    }
    #log-controls > * {
        margin-right: 1;
    }
    #log-display {
        height: 1fr;
        padding: 0 1;
        background: #000000;
    }
    """

    auto_scroll: reactive[bool] = reactive(True)
    is_paused: reactive[bool] = reactive(False)
    active_level: reactive[str] = reactive("ALL")
    active_component: reactive[str] = reactive("ALL")

    def __init__(self, debug_mode: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self._debug_mode = debug_mode
        self._file_offset = 0
        self._lines_buffer: list[dict[str, Any]] = []
        self._log_file_path = LOG_FILE
        self._components_seen: set[str] = {"ALL"}

    def compose(self) -> ComposeResult:
        with Horizontal(id="log-controls"):
            yield Select(
                [("ALL LEVELS", "ALL"), ("INFO", "INFO"), ("WARNING", "WARNING"), ("ERROR", "ERROR")],
                value="ALL",
                id="filter-level",
            )
            yield Select(
                [("ALL COMPONENTS", "ALL")],
                value="ALL",
                id="filter-component",
            )
            yield Checkbox("Auto-scroll", value=True, id="check-autoscroll")
            yield Button("Pause", id="btn-pause", variant="warning")
            yield Button("Clear", id="btn-clear")
        yield RichLog(
            id="log-display",
            max_lines=LOG_VIEWER_MAX_LINES,
            markup=True,
            wrap=True,
            auto_scroll=True,
            highlight=False,
        )

    def on_mount(self) -> None:
        """Start log polling interval."""
        # Read the tail of the log initially
        self.call_after_refresh(self._initial_read)
        # Poll every 1.0s
        self.set_interval(1.0, self._poll_logs)

    def _initial_read(self) -> None:
        """Read the last ~100 lines for immediate context."""
        try:
            if not os.path.exists(self._log_file_path):
                raise FileNotFoundError(self._log_file_path)
            with open(self._log_file_path, "r", encoding="utf-8") as f:
                # Naive tail implementation for small files
                lines = f.readlines()[-100:]
                self._file_offset = f.tell()
                if not lines:
                    raise ValueError("empty")
                for line in lines:
                    self._process_line(line.strip())
        except (FileNotFoundError, PermissionError, ValueError) as exc:
            # Show a helpful message instead of silent blank panel
            try:
                rl = self.query_one("#log-display", RichLog)
                is_missing = isinstance(exc, (FileNotFoundError, ValueError))
                if isinstance(exc, PermissionError):
                    rl.write(f"[bold red]✗ Permission denied:[/bold red] {self._log_file_path}")
                    rl.write("[dim]Run:[/dim] [cyan]sudo chmod g+r /var/log/sentinel/sentinel.log[/cyan]")
                else:
                    rl.write(f"[bold yellow]⏳ Waiting for log file:[/bold yellow] {self._log_file_path}")
                    rl.write("[dim]Daemon may still be starting. Logs appear automatically once the service writes its first entry.[/dim]")
                    rl.write("[dim]If you just installed, run:[/dim] [cyan]sudo systemctl start sentinel-backend[/cyan]")
            except Exception:
                pass
        except Exception:
            pass

    def _poll_logs(self) -> None:
        """Poll the file for new appended lines (Dev mode behavior)."""
        if self.is_paused:
            return

        try:
            with open(self._log_file_path, "r", encoding="utf-8") as f:
                f.seek(self._file_offset)
                new_lines = f.readlines()
                self._file_offset = f.tell()

                for line in new_lines:
                    self._process_line(line.strip())
        except Exception:
            pass

    def _process_line(self, raw_line: str) -> None:
        """Parse, filter, and render a single log line."""
        if not raw_line:
            return

        parsed = self._parse_line(raw_line)
        if not parsed: return

        # Update dynamic components dropdown if we see a new one
        comp = parsed.get("component")
        if comp and comp not in self._components_seen:
            self._components_seen.add(comp)
            try:
                sel = self.query_one("#filter-component", Select)
                opts = [("ALL COMPONENTS", "ALL")] + [(c, c) for c in sorted(list(self._components_seen)) if c != "ALL"]
                sel.set_options(opts)
            except Exception:
                pass # Select might not be ready

        # Apply Filters
        if self.active_level != "ALL" and parsed["level"] != self.active_level:
            return
        if self.active_component != "ALL" and parsed["component"] != self.active_component:
            return

        # Render
        self._render_line(parsed, raw_line)

    def _parse_line(self, raw: str) -> dict[str, Any] | None:
        """Attempt JSON parsing, fallback to Regex."""
        # 1. Try JSON
        if raw.startswith("{") and raw.endswith("}"):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass

        # 2. Try Regex
        match = _LOG_PATTERN.match(raw)
        if match:
            return match.groupdict()

        # 3. Unparseable plain text
        return {
            "timestamp": "",
            "level": "INFO",
            "component": "system",
            "message": raw,
        }

    def _render_line(self, parsed: dict[str, Any], raw: str) -> None:
        """Format and append line to RichLog."""
        rl = self.query_one("#log-display", RichLog)

        if self._debug_mode:
            # Show raw data
            rl.write(Text(raw, style="dim"))
            return

        level = parsed.get("level", "INFO").upper()
        comp  = parsed.get("component", "system")
        msg   = parsed.get("message", "")
        ts    = parsed.get("timestamp", "")

        # Truncate timestamp to time only if ISO format
        if "T" in ts:
            ts = ts.split("T")[-1].replace("Z", "")[:8]

        # Colors corresponding to app.tcss logic
        color_map = {
            "INFO": "bright_cyan",
            "WARNING": "bright_yellow",
            "ERROR": "bright_red",
            "CRITICAL": "bold red on black",
            "DEBUG": "dim",
        }
        lvl_color = color_map.get(level, "white")

        # Specific highlight for Auth results
        if "SUCCESS" in msg:
            msg_style = "bold bright_green"
        elif "FAILURE" in msg:
            msg_style = "bold bright_red"
        else:
            msg_style = "white"

        base_text = Text()
        base_text.append(f"[{ts}] ", style="dim")
        base_text.append(f"[{level.ljust(7)}] ", style=lvl_color)
        base_text.append(f"{comp.ljust(12)} │ ", style="bright_blue")
        base_text.append(msg, style=msg_style)

        rl.write(base_text)

    # ── Event Handlers ────────────────────────────────────────────────────────

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.control.id == "check-autoscroll":
            self.auto_scroll = event.value
            rl = self.query_one("#log-display", RichLog)
            rl.auto_scroll = event.value

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-pause":
            self.is_paused = not self.is_paused
            event.button.label = "Resume" if self.is_paused else "Pause"
            event.button.variant = "success" if self.is_paused else "warning"
        elif event.button.id == "btn-clear":
            self.query_one("#log-display", RichLog).clear()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.control.id == "filter-level":
            self.active_level = str(event.value)
        elif event.control.id == "filter-component":
            self.active_component = str(event.value)
