"""
enrollment.py — Multi-step wizard for capturing new faces.

Steps:
  1. Username Input
  2. Instructions
  3. Pose Capture Loop (5 poses)
  4. Success
"""

from __future__ import annotations

import logging
import subprocess
import time

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Input, Label

from sentinel_tui.constants import IPC_ENROLL_TIMEOUT, ErrorCode, DEFAULT_SOCKET_PATH
from sentinel_tui.services.ipc_client import SentinelIPCClient
from sentinel_tui.widgets.progress_bar import PoseProgress

logger = logging.getLogger(__name__)

# The 5 standard poses expected by Sentinel's BiometricProcessor
POSES = ["Center", "Left", "Right", "Up", "Down"]


class EnrollmentScreen(Container):
    """Wizard UI for face registration."""

    DEFAULT_CSS = """
    EnrollmentScreen {
        layout: vertical;
        align: center middle;
    }
    #enroll-container {
        width: 60;
        height: auto;
        min-height: 20;
        background: #111827;
        border: heavy #1e3a5f;
        padding: 2 4;
    }
    .wizard-title {
        color: #00d4ff;
        text-style: bold;
        border-bottom: solid #1e3a5f;
        padding-bottom: 1;
        margin-bottom: 2;
        content-align: center middle;
    }
    .wizard-step {
        display: none;
        height: auto;
        layout: vertical;
    }
    .wizard-step.--active {
        display: block;
    }
    .btn-row {
        layout: horizontal;
        height: 3;
        margin-top: 2;
        align: right middle;
    }
    .btn-row > Button {
        margin-left: 1;
    }
    #cam-status-box {
        height: 5;
        border: round #4b5563;
        content-align: center middle;
        margin: 1 0;
        background: #0a0e1a;
    }
    #cam-status-box.--ready { border: round #00ff88; color: #00ff88; }
    #cam-status-box.--warn  { border: round #ffaa00; color: #ffaa00; }
    #cam-status-box.--error { border: round #ff3366; color: #ff3366; }
    """

    def __init__(self, ipc: SentinelIPCClient, **kwargs) -> None:
        super().__init__(**kwargs)
        self._ipc = ipc
        self._username = ""
        self._pose_loop_active = False
        self._preview_proc = None  # OpenCV preview subprocess

    def compose(self) -> ComposeResult:
        with Container(id="enroll-container"):
            yield Label("Face Enrollment Wizard", classes="wizard-title")

            # --- STEP 1: Username ---
            with Vertical(id="step-1", classes="wizard-step --active"):
                yield Label("Enter a single-word username for the new profile:")
                yield Input(placeholder="e.g., admin", id="input-username", restrict=r"^[A-Za-z0-9_-]+$")
                yield Label("", id="err-username", classes="label--error")
                with Horizontal(classes="btn-row"):
                    yield Button("Next", id="btn-to-step2", variant="primary")

            # --- STEP 2: Instructions ---
            with Vertical(id="step-2", classes="wizard-step"):
                yield Label("Instructions:", classes="label--title")
                yield Label("1. Ensure you are in a well-lit area.\n2. Look directly at the camera.\n3. The system will guide you through 5 head poses.\n4. Hold each pose until captured.")
                with Horizontal(classes="btn-row"):
                    yield Button("Cancel", id="btn-cancel-1", variant="error")
                    yield Button("Start Camera", id="btn-to-step3", variant="primary")

            # --- STEP 3: Capture Loop ---
            with Vertical(id="step-3", classes="wizard-step"):
                yield PoseProgress(POSES, id="pose-progress")
                yield Label("Waiting for camera...", id="pose-instruction", classes="label--title")
                yield Label("", id="cam-status-box")
                with Horizontal(classes="btn-row"):
                    yield Button("Cancel", id="btn-cancel-2", variant="error")
                    yield Button("Capture Pose", id="btn-capture", variant="success", disabled=True)

            # --- STEP 4: Success ---
            with Vertical(id="step-4", classes="wizard-step"):
                yield Label("✅ Enrollment Complete!", classes="label--success", id="lbl-success-title")
                yield Label("The face profile has been securely saved to the gallery.", classes="label--muted")
                with Horizontal(classes="btn-row"):
                    yield Button("Done", id="btn-done", variant="primary")

    # ── Visibility Helpers ────────────────────────────────────────────────────

    def _show_step(self, step_num: int) -> None:
        for i in range(1, 5):
            try:
                step = self.query_one(f"#step-{i}", Vertical)
                if i == step_num:
                    step.add_class("--active")
                else:
                    step.remove_class("--active")
            except Exception:
                pass

    # ── Interaction & IPC ─────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id

        if btn_id == "btn-to-step2":
            self._validate_username_and_proceed()
        elif btn_id == "btn-to-step3":
            self._start_enrollment_process()
        elif btn_id == "btn-capture":
            self._capture_current_pose()
        elif btn_id in ("btn-cancel-1", "btn-cancel-2"):
            self._abort_enrollment()
        elif btn_id == "btn-done":
            # Reset and go back to dashboard
            self._show_step(1)
            self.query_one("#input-username", Input).value = ""
            self.app.action_show_screen("dashboard")

    def _validate_username_and_proceed(self) -> None:
        inp = self.query_one("#input-username", Input).value.strip()
        err = self.query_one("#err-username", Label)
        
        if not inp:
            err.update("Username cannot be empty")
            return
            
        self._username = inp
        err.update("")
        self.notify("Checking gallery...")
        self.run_worker(self._do_check_username, thread=True)

    def _do_check_username(self) -> None:
        res = self._ipc.call("get_enrolled_users")
        users = res.get("users", [])
        
        def _update_ui():
            if self._username in users:
                self.query_one("#err-username", Label).update(f"User '{self._username}' already exists!")
            else:
                self._show_step(2)
        
        self.app.call_from_thread(_update_ui)

    def _start_enrollment_process(self) -> None:
        self._show_step(3)
        self.query_one("#pose-instruction", Label).update("Initializing Camera...")
        self.query_one("#cam-status-box", Label).update("Warming up models...")
        self.run_worker(self._do_start_enrollment, thread=True)

    def _do_start_enrollment(self) -> None:
        # NOTE: daemon reads param as 'user_name', not 'username'
        res = self._ipc.call("start_enrollment", {"user_name": self._username}, timeout=IPC_ENROLL_TIMEOUT)
        
        def _handle_start():
            if res.get("success"):
                # Reset UI state
                self.query_one("#pose-progress", PoseProgress).current_step = 0
                self._pose_loop_active = True
                self.set_interval(0.3, self._poll_frame)
                # Launch live OpenCV preview window
                self._launch_preview()
            else:
                error_msg = res.get("error", res.get("message", ""))
                code = res.get("error_code", "UNKNOWN")
                desc = error_msg if error_msg else ErrorCode.describe(code)
                self.notify(f"Cannot start: {desc}", severity="error")
                self._show_step(1)
        
        self.app.call_from_thread(_handle_start)

    def _poll_frame(self) -> None:
        """Called every 300ms to update the camera status box."""
        if not self._pose_loop_active:
            return
        self.run_worker(self._do_poll_frame, thread=True, exclusive=True)

    def _do_poll_frame(self) -> None:
        res = self._ipc.call("process_enroll_frame", timeout=IPC_ENROLL_TIMEOUT)
        
        def _update_ui():
            if not self._pose_loop_active: return
            
            box = self.query_one("#cam-status-box", Label)
            btn = self.query_one("#btn-capture", Button)
            instr = self.query_one("#pose-instruction", Label)
            prog = self.query_one("#pose-progress", PoseProgress)
            
            if not res.get("success"):
                box.update("Camera Disconnected")
                box.remove_class("--ready", "--warn")
                box.add_class("--error")
                btn.disabled = True
                return
                
            status = res.get("status", "unknown")
            pose_idx = res.get("current_pose", 0)
            pose_name = POSES[pose_idx] if pose_idx < len(POSES) else "Done"
            
            prog.current_step = pose_idx
            instr.update(f"Action: Look {pose_name}")
            
            if status == "ready":
                box.update(f"✓ Face Detected — Hold still")
                box.remove_class("--warn", "--error")
                box.add_class("--ready")
                btn.disabled = False
            elif status == "no_face":
                box.update(f"✗ No face detected")
                box.remove_class("--ready", "--error")
                box.add_class("--warn")
                btn.disabled = True
            elif status == "multiple_faces":
                box.update(f"⚠ Multiple faces detected")
                box.remove_class("--ready", "--error")
                box.add_class("--warn")
                btn.disabled = True
            elif status == "face_too_small":
                box.update(f"⚠ Face too far away. Move closer.")
                box.remove_class("--ready", "--error")
                box.add_class("--warn")
                btn.disabled = True
        
        self.app.call_from_thread(_update_ui)

    def _capture_current_pose(self) -> None:
        self.query_one("#btn-capture", Button).disabled = True
        self.run_worker(self._do_capture, thread=True)

    def _do_capture(self) -> None:
        res = self._ipc.call("capture_enroll_pose", timeout=IPC_ENROLL_TIMEOUT)
        
        def _handle_capture():
            if res.get("success"):
                completed = res.get("completed", False)
                if completed:
                    self._pose_loop_active = False
                    self._show_step(4)
            else:
                self.notify(f"Capture failed: {res.get('error')}", severity="warning")
        
        self.app.call_from_thread(_handle_capture)

    def _abort_enrollment(self) -> None:
        self._pose_loop_active = False
        self._kill_preview()
        self.notify("Canceling enrollment...")
        self.run_worker(lambda: self._ipc.call("stop_enrollment"), thread=True)
        self._show_step(1)
        self.app.action_show_screen("dashboard")

    def _launch_preview(self) -> None:
        """Launch frame_preview as a detached module (works from any cwd)."""
        try:
            self._kill_preview()
            self._preview_proc = subprocess.Popen(
                ["uv", "run", "python", "-m",
                 "sentinel_tui.scripts.frame_preview",
                 "--mode", "enroll",
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
        """Clean up resources when screen is destroyed.

        IMPORTANT: Do NOT call _abort_enrollment() here — that calls
        action_show_screen() which triggers navigation during screen
        destruction and causes a double-unmount / layout crash.
        Instead, we fire the stop IPC call directly and clean up state.
        """
        self._pose_loop_active = False
        self._kill_preview()
        if self.current_mode_was_active():
            self.run_worker(lambda: self._ipc.call("stop_enrollment"), thread=True)

    def current_mode_was_active(self) -> bool:
        """Check if enrollment was in progress without querying DOM (safe from on_unmount)."""
        return self._username != ""
