"""
status_indicator.py — Reusable colored status indicator widget.

Usage:
    indicator = StatusIndicator("service", "running")
    indicator.set_state("error", error_code="CAMERA_NOT_FOUND")
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label

from sentinel_tui.constants import ErrorCode


class StatusIndicator(Widget):
    """
    A compact colored dot + label pair.

    States:
      healthy  → ● green
      degraded → ⚠ amber
      critical / error → ✗ red
      loading  → ⏳ amber (pulsing via CSS animation)
      stopped  → ○ gray
    """

    DEFAULT_CSS = """
    StatusIndicator {
        height: 1;
        width: auto;
        layout: horizontal;
    }
    StatusIndicator .dot {
        width: 2;
        margin-right: 1;
    }
    StatusIndicator .dot--healthy  { color: #00ff88; }
    StatusIndicator .dot--degraded { color: #ffaa00; }
    StatusIndicator .dot--critical { color: #ff3366; }
    StatusIndicator .dot--error    { color: #ff3366; }
    StatusIndicator .dot--loading  { color: #ffaa00; }
    StatusIndicator .dot--stopped  { color: #4b5563; }
    StatusIndicator .label { color: #e2e8f0; }
    """

    _DOT_CHARS: dict[str, str] = {
        "healthy":  "●",
        "degraded": "⚠",
        "critical": "✗",
        "error":    "✗",
        "loading":  "⏳",
        "stopped":  "○",
    }

    state: reactive[str] = reactive("stopped")
    label_text: reactive[str] = reactive("")

    def __init__(
        self,
        name: str,
        initial_state: str = "stopped",
        label: str = "",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._name = name
        self.state = initial_state
        self.label_text = label or name.capitalize()
        self._error_code: str | None = None
        self._tooltip_text: str = ""

    def compose(self) -> ComposeResult:
        yield Label(self._DOT_CHARS.get(self.state, "○"), classes=f"dot dot--{self.state}", id=f"{self._name}-dot")
        yield Label(self.label_text, classes="label", id=f"{self._name}-label")

    def set_state(
        self,
        state: str,
        label: str | None = None,
        error_code: str | None = None,
    ) -> None:
        """
        Update the indicator state.

        Args:
            state:      One of: healthy, degraded, critical, error, loading, stopped
            label:      Override display label text
            error_code: ErrorCode string — appended to tooltip
        """
        self.state = state
        self._error_code = error_code
        if label:
            self.label_text = label

        if error_code:
            self._tooltip_text = ErrorCode.describe(error_code)
            self.tooltip = self._tooltip_text

        # Update live DOM elements
        try:
            dot = self.query_one(f"#{self._name}-dot", Label)
            dot.update(self._DOT_CHARS.get(state, "○"))
            dot.remove_class(*[f"dot--{s}" for s in self._DOT_CHARS])
            dot.add_class(f"dot--{state}")

            lbl = self.query_one(f"#{self._name}-label", Label)
            lbl.update(self.label_text)
        except Exception:
            pass  # Widget may not be mounted yet during reactive updates
