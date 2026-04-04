"""
authentication.py — Real-time authentication testing screen.

Simulates the PAM authentication process within the TUI to verify:
  - Face recognition accuracy
  - Liveness checking
  - Security thresholds (Spoof rejection, Confidence score)
"""

from __future__ import annotations

import logging
import subprocess

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Label, Select

from sentinel_tui.constants import IPC_AUTH_TIMEOUT, IPC_READ_TIMEOUT, ErrorCode, DEFAULT_SOCKET_PATH
from sentinel_tui.services.ipc_client import SentinelIPCClient

logger = logging.getLogger(__name__)

class AuthenticationScreen(Container):
    """
    Test tool for the authentication pipeline.
    """

    DEFAULT_CSS = """
    AuthenticationScreen {
        layout: vertical;
        align: center middle;
    }
    #auth-container {
        width: 80;
        height: auto;
        min-height: 30;
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
        self._preview_proc = None  # OpenCV preview subprocess

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

                with Horizontal(classes="info-row"):
                    yield Label("Recognition Threshold:", classes="info-label")
                    yield Label("—", id="lbl-threshold", classes="info-value")

                with Horizontal(classes="info-row"):
                    yield Label("Task Progress:", classes="info-label")
                    yield Label("—", id="lbl-task", classes="info-value")

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

        def _update():
            sel = self.query_one("#select-user", Select)
            if not res.get("success"):
                # IPC not ready yet — retry in 3 seconds
                sel.set_options([("Connecting to daemon...", "")])
                sel.disabled = True
                self.query_one("#btn-start", Button).disabled = True
                self.set_timer(3.0, self._refresh_users)
                return

            users = res.get("users", [])
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
        self.query_one("#lbl-threshold", Label).update("—")
        self.query_one("#lbl-task", Label).update("—")
        self.query_one("#score-fill", Label).styles.width = "0%"

        self._auth_active = True
        self.run_worker(lambda: self._do_start(username), thread=True)
        # Launch live frame preview window as a detached subprocess
        self._launch_preview("auth")

    def _do_start(self, username: str) -> None:
        # NOTE: daemon reads param as 'user', not 'username'
        res = self._ipc.call("start_authentication", {"user": username}, timeout=IPC_READ_TIMEOUT)

        def _handle():
            if res.get("success"):
                self.set_interval(0.3, self._poll_frame)  # 300ms: prevents worker pileup on slow frames
            else:
                error_msg = res.get("error", res.get("message", ""))
                code = res.get("error_code", "UNKNOWN")
                # Show specific error if we have one, else fall back to error code description
                display = error_msg if error_msg else ErrorCode.describe(code)
                self.notify(f"Failed to start: {display}", severity="error")
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
                err = res.get("error", res.get("message", "Daemon connection lost"))
                self._stop_test(error=f"Camera error: {err}")
                return
                
            state = res.get("state", "WAITING")
            # Daemon sends current_distance inside the 'info' dict
            info = res.get("info", {})
            dist = info.get("dist", res.get("current_distance", 1.0))
            
            # Map distance (0=perfect, 1=no match) to a visual score 0-100%
            # Typically 0.4 is the threshold. We'll make 0.4 = 50% visual bar limit
            visual_score = max(0, min(100, int((1.0 - (dist * 1.5)) * 100)))
            
            self.query_one("#lbl-score", Label).update(f"{dist:.3f}")
            self.query_one("#score-fill", Label).styles.width = f"{visual_score}%"
            
            # Update threshold display based on distance
            threshold_lbl = self.query_one("#lbl-threshold", Label)
            if dist < 0.25:
                threshold_lbl.update("Golden (< 0.25)")
                threshold_lbl.styles.color = "#ffd700"  # Gold
            elif dist < 0.42:
                threshold_lbl.update("Standard (< 0.42)")
                threshold_lbl.styles.color = "#00ff88"  # Green
            elif dist < 0.50:
                threshold_lbl.update("Two-Factor (< 0.50)")
                threshold_lbl.styles.color = "#ffaa00"  # Orange
            else:
                threshold_lbl.update("Above Threshold")
                threshold_lbl.styles.color = "#ff3366"  # Red
            
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
            box.remove_class("--warning")
            task_lbl = self.query_one("#lbl-task", Label)
            
            if state in ("SUCCESS", "FAILURE", "LOCKOUT"):
                self._handle_completion(state, res)
            elif state == "ERROR":
                # Camera freeze or processing error — surface the daemon's message
                err_msg = res.get("message", "Camera error — check daemon logs")
                self._stop_test(error=err_msg)
            elif state == "RECOGNIZED":
                # Show the instruction/challenge message prominently
                message = res.get("message", "").strip()
                box.update(message if message else "Face Recognized! Complete challenges...")
                # Extract challenge type for task display using actual backend message text
                msg_lower = message.lower()
                if "please turn" in msg_lower and "right" in msg_lower:
                    task_lbl.update("↗️  Turn Head Right")
                elif "please turn" in msg_lower and "left" in msg_lower:
                    task_lbl.update("↖️  Turn Head Left")
                elif "please turn" in msg_lower and "up" in msg_lower:
                    task_lbl.update("⬆️  Turn Head Up")
                elif "please turn" in msg_lower and "down" in msg_lower:
                    task_lbl.update("⬇️  Turn Head Down")
                elif "blink" in msg_lower:
                    task_lbl.update("👁️  Blink Once")
                elif "face detected" in msg_lower:
                    task_lbl.update("👁️  Face Detected")
                else:
                    task_lbl.update("👁️  Perform challenge")
                box.add_class("--warning")
            elif state == "REQUIRE_2FA" or state == "STATE_2FA":
                message = res.get("message", "").strip()
                box.update(message if message else "2FA Required: Please enter your password")
                task_lbl.update("🔐 Password Required")
                box.add_class("--warning")
            elif state == "WAITING":
                message = res.get("message", "").strip()
                box.update(message if message else "Looking for face...")
                task_lbl.update("👁️  Position Face")
            else:
                message = res.get("message", "").strip()
                box.update(message if message else "Waiting for face...")
                task_lbl.update("—")

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
        self._kill_preview()
        
        if error:
            self.notify(error, severity="error")
            self._show_panel("setup")
        elif aborted:
            self.notify("Test aborted manually", severity="warning")
            self._show_panel("setup")

    def _launch_preview(self, mode: str) -> None:
        """Launch frame_preview as a detached module (works from any cwd)."""
        try:
            self._kill_preview()
            self._preview_proc = subprocess.Popen(
                ["uv", "run", "python", "-m",
                 "sentinel_tui.scripts.frame_preview",
                 "--mode", mode,
                 "--socket", DEFAULT_SOCKET_PATH],
                start_new_session=True
            )
        except Exception as e:
            logger.warning(f"Could not launch frame preview: {e}")

    def _kill_preview(self) -> None:
        """Terminate the preview subprocess if running."""
        if self._preview_proc and self._preview_proc.poll() is None:
            try:
                self._preview_proc.terminate()
            except Exception:
                pass
        self._preview_proc = None

    def on_unmount(self) -> None:
        if self._auth_active:
            self._stop_test(aborted=True)
        self._kill_preview()
