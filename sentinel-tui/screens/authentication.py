"""
authentication.py — Real-time authentication testing screen.

Simulates the PAM authentication process within the TUI to verify:
  - Face recognition accuracy
  - Liveness checking
  - Security thresholds (Spoof rejection, Confidence score)
"""

from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Label, Select

from sentinel_tui.constants import IPC_AUTH_TIMEOUT, IPC_READ_TIMEOUT, ErrorCode
from sentinel_tui.services.ipc_client import SentinelIPCClient

logger = logging.getLogger(__name__)

class AuthenticationScreen(Screen):
    """
    Test tool for the authentication pipeline.
    """

    DEFAULT_CSS = """
    AuthenticationScreen {
        layout: vertical;
        align: center middle;
    }
    #auth-container {
        width: 60;
        height: auto;
        min-height: 25;
        background: #111827;
        border: heavy #1e3a5f;
        padding: 2 4;
    }
    .auth-title {
        color: #00d4ff;
        text-style: bold;
        border-bottom: solid #1e3a5f;
        padding-bottom: 1;
        margin-bottom: 2;
        content-align: center middle;
    }
    .auth-panel {
        display: none;
        height: auto;
        layout: vertical;
    }
    .auth-panel.--active {
        display: block;
    }
    .info-row {
        layout: horizontal;
        height: 1;
        margin: 1 0;
    }
    .info-label { width: 22; color: #94a3b8; }
    .info-value { width: 1fr; color: #e2e8f0; text-style: bold; }
    
    #score-bar {
        height: 1;
        background: #0a0e1a;
        margin: 1 0;
    }
    #score-fill {
        height: 1;
        width: 0%;
        background: #00ff88;
    }
    
    #live-status-box {
        height: 5;
        border: round #4b5563;
        content-align: center middle;
        margin: 2 0;
        background: #0a0e1a;
    }
    #live-status-box.--success  { border: round #00ff88; color: #00ff88; }
    #live-status-box.--failure  { border: round #ff3366; color: #ff3366; }
    #live-status-box.--warning  { border: round #ffaa00; color: #ffaa00; }
    #live-status-box.--active   { border: round #00d4ff; color: #00d4ff; }
    """

    def __init__(self, ipc: SentinelIPCClient, **kwargs) -> None:
        super().__init__(**kwargs)
        self._ipc = ipc
        self._auth_active = False

    def compose(self) -> ComposeResult:
        with Container(id="auth-container"):
            yield Label("Authentication Test Engine", classes="auth-title")

            # --- PRE-TEST PANEL ---
            with Vertical(id="panel-setup", classes="auth-panel --active"):
                yield Label("Select an enrolled user to test:")
                yield Select([], id="select-user")
                
                with Horizontal(classes="btn-row"):
                    yield Button("Start Test", id="btn-start", variant="primary")
                    yield Button("Cancel", id="btn-cancel-setup", variant="error")

            # --- LIVE TEST PANEL ---
            with Vertical(id="panel-live", classes="auth-panel"):
                with Horizontal(classes="info-row"):
                    yield Label("Target User:", classes="info-label")
                    yield Label("—", id="lbl-target-user", classes="info-value")
                
                with Horizontal(classes="info-row"):
                    yield Label("Confidence Score:", classes="info-label")
                    yield Label("0.00", id="lbl-score", classes="info-value")
                    yield Label("", id="lbl-zone", classes="zone-badge")

                with Horizontal(id="score-bar"):
                    yield Label("", id="score-fill")

                yield Label("Waiting for camera...", id="live-status-box")
                
                with Horizontal(classes="btn-row"):
                    yield Button("Abort Test", id="btn-abort", variant="error")

            # --- RESULTS PANEL ---
            with Vertical(id="panel-results", classes="auth-panel"):
                yield Label("Test Complete", classes="section-header")
                
                with Horizontal(classes="info-row"):
                    yield Label("Final Decision:", classes="info-label")
                    yield Label("—", id="res-decision", classes="info-value")
                    
                with Horizontal(classes="info-row"):
                    yield Label("Time Elapsed:", classes="info-label")
                    yield Label("—", id="res-time", classes="info-value")
                    
                with Horizontal(classes="btn-row"):
                    yield Button("Run Again", id="btn-restart", variant="primary")
                    yield Button("Dashboard", id="btn-home")

    # ── Visibility ────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self._refresh_users()

    def _refresh_users(self) -> None:
        self.run_worker(self._do_fetch_users, thread=True)

    def _do_fetch_users(self) -> None:
        res = self._ipc.call("get_enrolled_users", timeout=IPC_READ_TIMEOUT)
        users = res.get("users", [])
        
        def _update():
            sel = self.query_one("#select-user", Select)
            if not users:
                sel.set_options([("No users found (Go to Enrollment)", "")])
                sel.disabled = True
                self.query_one("#btn-start", Button).disabled = True
            else:
                sel.set_options([(u, u) for u in users])
                sel.disabled = False
                self.query_one("#btn-start", Button).disabled = False

        self.app.call_from_thread(_update)

    def _show_panel(self, panel_id: str) -> None:
        for p in ("setup", "live", "results"):
            try:
                elem = self.query_one(f"#panel-{p}", Vertical)
                if p == panel_id:
                    elem.add_class("--active")
                else:
                    elem.remove_class("--active")
            except Exception:
                pass

    # ── Interaction ───────────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-start":
            user = self.query_one("#select-user", Select).value
            if not user: return
            self._start_test(str(user))
        elif btn_id == "btn-abort":
            self._stop_test(aborted=True)
        elif btn_id in ("btn-cancel-setup", "btn-home"):
            self.app.action_show_screen("dashboard")
        elif btn_id == "btn-restart":
            self._show_panel("setup")

    def _start_test(self, username: str) -> None:
        self.query_one("#lbl-target-user", Label).update(username)
        self._show_panel("live")
        
        # Reset UI
        box = self.query_one("#live-status-box", Label)
        box.update("Initializing Authentication Engine...")
        box.remove_class("--success", "--failure", "--warning")
        box.add_class("--active")
        
        self.query_one("#lbl-score", Label).update("0.00")
        self.query_one("#lbl-zone", Label).update("")
        self.query_one("#score-fill", Label).styles.width = "0%"

        self._auth_active = True
        self.run_worker(lambda: self._do_start(username), thread=True)

    def _do_start(self, username: str) -> None:
        res = self._ipc.call("start_authentication", {"username": username}, timeout=IPC_READ_TIMEOUT)
        
        def _handle():
            if res.get("success"):
                self.set_interval(0.2, self._poll_frame)
            else:
                code = res.get("error_code", "UNKNOWN")
                self.notify(f"Failed to start: {ErrorCode.describe(code)}", severity="error")
                self._auth_active = False
                self._show_panel("setup")
                
        self.app.call_from_thread(_handle)

    def _poll_frame(self) -> None:
        if not self._auth_active: return
        self.run_worker(self._do_poll_frame, thread=True, exclusive=True)

    def _do_poll_frame(self) -> None:
        res = self._ipc.call("process_auth_frame", timeout=IPC_AUTH_TIMEOUT)
        
        def _update():
            if not self._auth_active: return
            
            if not res.get("success"):
                self._stop_test(error="Daemon connection lost")
                return
                
            state = res.get("state", "WAITING")
            dist = res.get("current_distance", 1.0)
            
            # Map distance (0=perfect, 1=no match) to a visual score 0-100%
            # Typically 0.4 is the threshold. We'll make 0.4 = 50% visual bar limit
            visual_score = max(0, min(100, int((1.0 - (dist * 1.5)) * 100)))
            
            self.query_one("#lbl-score", Label).update(f"{dist:.3f}")
            self.query_one("#score-fill", Label).styles.width = f"{visual_score}%"
            
            zn = self.query_one("#lbl-zone", Label)
            zn.remove_class("zone-badge--golden", "zone-badge--standard", "zone-badge--2fa", "zone-badge--failure")
            
            if dist < 0.30:
                zn.update("GOLDEN")
                zn.add_class("zone-badge--golden")
            elif dist < 0.42:
                zn.update("STANDARD")
                zn.add_class("zone-badge--standard")
            elif dist < 0.50:
                zn.update("REQUIRE LIVENESS")
                zn.add_class("zone-badge--2fa")
            else:
                zn.update("FAILURE")
                zn.add_class("zone-badge--failure")

            box = self.query_one("#live-status-box", Label)
            
            if state in ("SUCCESS", "FAILURE", "LOCKOUT"):
                self._handle_completion(state, res)
            elif state == "RECOGNIZED":
                box.update("Face Recognized! Checking Liveness...")
            elif state == "LIVENESS_CHALLENGE":
                chal = res.get("liveness_challenge", "")
                box.update(f"Liveness Check:\n\nPlease nod your head {chal}")
                box.add_class("--warning")
            else:
                box.update("Waiting for face...")

        self.app.call_from_thread(_update)

    def _handle_completion(self, final_state: str, result_data: dict) -> None:
        self._auth_active = False
        self.run_worker(lambda: self._ipc.call("stop_authentication"), thread=True)
        
        decision = self.query_one("#res-decision", Label)
        
        if final_state == "SUCCESS":
            decision.update("ACCESS GRANTED ✅")
            decision.styles.color = "#00ff88"
        elif final_state == "FAILURE":
            decision.update("ACCESS DENIED ❌")
            decision.styles.color = "#ff3366"
        elif final_state == "LOCKOUT":
            decision.update("USER LOCKED OUT 🔒")
            decision.styles.color = "#ff3366"

        # In a real app we'd parse timestamps to get elapsed time
        self.query_one("#res-time", Label).update("Test finished")
        self._show_panel("results")

    def _stop_test(self, aborted: bool = False, error: str = "") -> None:
        self._auth_active = False
        self.run_worker(lambda: self._ipc.call("stop_authentication"), thread=True)
        
        if error:
            self.notify(error, severity="error")
            self._show_panel("setup")
        elif aborted:
            self.notify("Test aborted manually", severity="warning")
            self._show_panel("setup")

    def on_unmount(self) -> None:
        if self._auth_active:
            self._stop_test(aborted=True)
