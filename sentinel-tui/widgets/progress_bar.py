"""
progress_bar.py — Stylized step indicator for the Face Enrollment wizard.

Displays: Center → Left → Right → Up → Down
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label


class PoseProgress(Widget):
    """
    Shows a sequence of steps, highlighting the current one.
    """

    DEFAULT_CSS = """
    PoseProgress {
        height: 3;
        layout: horizontal;
        margin: 1 0;
        align: center middle;
    }
    .pose-step {
        width: 1fr;
        height: 3;
        border: round #1e3a5f;
        content-align: center middle;
        color: #4b5563;
        margin: 0 1;
    }
    .pose-step--done {
        border: round #00ff88;
        color: #00ff88;
        background: #0e4a2a;
    }
    .pose-step--active {
        border: round #00d4ff;
        color: #00d4ff;
        background: #0e2a4a;
        text-style: bold;
    }
    """

    current_step: reactive[int] = reactive(0)

    def __init__(self, steps: list[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self._steps = steps

    def compose(self) -> ComposeResult:
        with Horizontal(id="steps-container"):
            for i, step_name in enumerate(self._steps):
                yield Label(
                    f"○ {step_name}", 
                    id=f"step-{i}", 
                    classes="pose-step"
                )

    def watch_current_step(self, old_val: int, new_val: int) -> None:
        """Called automatically by Textual when current_step changes."""
        try:
            for i, step_name in enumerate(self._steps):
                lbl = self.query_one(f"#step-{i}", Label)
                lbl.remove_class("pose-step--active", "pose-step--done")
                
                if i < new_val:
                    lbl.add_class("pose-step--done")
                    lbl.update(f"✓ {step_name}")
                elif i == new_val:
                    lbl.add_class("pose-step--active")
                    lbl.update(f"► {step_name}")
                else:
                    lbl.update(f"○ {step_name}")
        except Exception:
            pass  # DOM might not be fully ready
