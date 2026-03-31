"""
dashboard.py — Main dashboard screen using the new health endpoint and structured logs.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import timedelta

from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Label, Static

from sentinel_tui.constants import DASHBOARD_HEALTH_INTERVAL, ErrorCode, IPC_READ_TIMEOUT
from sentinel_tui.services.ipc_client import SentinelIPCClient
from sentinel_tui.widgets.log_viewer import LogViewer
from sentinel_tui.widgets.status_indicator import StatusIndicator

logger = logging.getLogger(__name__)


class StatusCard(Static):
    """A standard metrics/status card for the dashboard top row."""
    DEFAULT_CSS = """
    StatusCard {
        background: #111827;
        border: round #1e3a5f;
        padding: 1 2;
        height: auto;
    }
    """
    def __init__(self, title: str, id: str, initial_value: str = "—", **kwargs) -> None:
        super().__init__(id=id, **kwargs)
        self._title = title
        self._val = initial_value

    def compose(self) -> ComposeResult:
        yield Label(self._title, classes="card-title")
        yield Label(self._val, id=f"{self.id}-value", classes="card-value card-value--loading")

    def update_value(self, value: str, state_class: str = "stopped") -> None:
        """Update the value text and apply a color class (e.g., healthy, critical)."""
        try:
            lbl = self.query_one(f"#{self.id}-value", Label)
            lbl.update(value)
            lbl.remove_class("card-value--healthy", "card-value--degraded", "card-value--critical", "card-value--loading", "card-value--stopped")
            if state_class:
                lbl.add_class(f"card-value--{state_class}")
        except Exception:
            pass


class DashboardScreen(Screen):
    """
    Primary monitoring and oversight screen.
    Layout:
      - Top Grid (Status Cards)
      - Middle Horizontal (Actions & Recent Events)
      - Bottom (Log Viewer)
    """
    
    def __init__(self, ipc: SentinelIPCClient, debug: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self._ipc = ipc
        self._debug = debug

    def compose(self) -> ComposeResult:
        with Vertical(id="dashboard-container"):
            yield Label("System Overview", classes="section-header")

            # Top Row — Status Cards
            with Grid(id="status-grid", classes="status-cards"):
                yield StatusCard("SERVICE", "card-service", "Loading...")
                yield StatusCard("MODELS",  "card-models",  "Loading...")
                yield StatusCard("CAMERA",  "card-camera",  "Loading...")
                yield StatusCard("GALLERY", "card-gallery", "Loading...")

            yield Label("Actions & Context", classes="section-header")

            # Middle Row — Actions
            with Horizontal(id="actions-row", classes="actions-bar"):
                yield Button("Launch Camera Preview", id="btn-preview", variant="primary")
                yield Button("Initialize Models", id="btn-init")
                
                with Vertical(id="events-panel"):
                    yield Label("Live Context:", classes="label--muted")
                    yield Label("Uptime: — | Config: v—", id="label-context", classes="label--title")

            yield Label("Live Logs", classes="section-header")

            # Bottom Row — Logs
            yield LogViewer(debug_mode=self._debug, id="main-log-viewer")

    def on_mount(self) -> None:
        """Start polling loop for health data."""
        self._refresh_health()
        self.set_interval(DASHBOARD_HEALTH_INTERVAL, self._refresh_health)

    def _refresh_health(self) -> None:
        """Hits the `health` RPC endpoint and updates UI."""
        self.run_worker(self._do_health_check, thread=True, exclusive=True)

    def _do_health_check(self) -> None:
        result = self._ipc.call("health", timeout=IPC_READ_TIMEOUT)
        self.call_from_thread(self._update_cards, result)

    def _update_cards(self, result: dict) -> None:
        """DOM update thread."""
        success = result.get("success", False)

        def get_card(cid: str) -> StatusCard | None:
            try:
                return self.query_one(f"#{cid}", StatusCard)
            except Exception:
                return None

        card_service = get_card("card-service")
        card_models  = get_card("card-models")
        card_camera  = get_card("card-camera")
        card_gallery = get_card("card-gallery")

        if not success:
            # Daemon Unreachable or Error
            code = result.get("error_code", ErrorCode.UNKNOWN)
            if card_service: card_service.update_value("DISCONNECTED", "critical")
            if card_models:  card_models.update_value("UNKNOWN", "stopped")
            if card_camera:  card_camera.update_value("UNKNOWN", "stopped")
            if card_gallery: card_gallery.update_value("UNKNOWN", "stopped")
            return

        # Daemon Reachable -> Map health values
        stat = result.get("status", "unknown")
        mods = result.get("models", "unknown")
        cam  = result.get("camera", "unknown")
        usrs = result.get("enrolled_users", 0)

        if card_service:
            # Service status: healthy / degraded / critical
            cls = "healthy" if stat == "healthy" else ("degraded" if stat == "degraded" else "critical")
            card_service.update_value(stat.upper(), cls)

        if card_models:
            cls = "healthy" if mods == "loaded" else ("degraded" if mods == "loading" else "critical")
            card_models.update_value(mods.upper().replace("_", " "), cls)
            
            # Toggle initialize button
            try:
                btn_init = self.query_one("#btn-init", Button)
                btn_init.disabled = (mods == "loaded")
            except Exception:
                pass

        if card_camera:
            cls = "healthy" if cam == "ok" else "critical"
            val = "CONNECTED" if cam == "ok" else cam.upper()
            card_camera.update_value(val, cls)

        if card_gallery:
            cls = "healthy" if usrs > 0 else "degraded"
            card_gallery.update_value(f"{usrs} USERS", cls)

        # Update Live Context
        uptime_sec = result.get("uptime_seconds", 0)
        uptime_str = str(timedelta(seconds=int(uptime_sec)))
        try:
            cfg_ver = result.get("config_version", "—")
            self.query_one("#label-context", Label).update(f"Uptime: {uptime_str} | Config: v{cfg_ver}")
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-preview":
            self.notify("Launching Camera Preview window...", title="Preview")
            self._launch_preview()
        elif event.button.id == "btn-init":
            self.notify("Sending initialization command to models...", title="Models")
            self.run_worker(self._do_init, thread=True)

    def _do_init(self) -> None:
        """Call 'initialize' RPC."""
        res = self._ipc.call("initialize")
        if res.get("success"):
            self.app.call_from_thread(self.notify, "Models initialized successfully.", title="Success")
            self.app.call_from_thread(self._refresh_health)
        else:
            code = res.get("error_code", "UNKNOWN")
            self.app.call_from_thread(self.notify, f"Init failed: {code}", severity="error")

    def _launch_preview(self) -> None:
        """Spawn preview subprocess."""
        try:
            subprocess.Popen(
                ["uv", "run", "python", "sentinel-tui/scripts/camera_preview.py"],
                start_new_session=True  # Detach from TUI process group so Ctrl-C in TUI doesn't kill it abruptly
            )
        except Exception as e:
            logger.error(f"Failed to launch preview: {e}")
            self.notify(f"Could not launch preview window: {e}", severity="error")
