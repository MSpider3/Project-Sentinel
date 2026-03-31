"""
settings.py — Settings manager screen with real-time validation and config.ini persistence.
"""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Static
from textual.message import Message

from sentinel_tui.constants import EXPECTED_CONFIG_VERSION, IPC_READ_TIMEOUT, ErrorCode
from sentinel_tui.services.config_manager import ConfigSchema
from sentinel_tui.services.ipc_client import SentinelIPCClient

logger = logging.getLogger(__name__)

# Form field mapping (Section -> List of Fields)
FORM_SECTIONS = {
    "Camera Hardware": [
        ("camera_device_id", "Device ID (/dev/videoN)"),
        ("camera_width", "Resolution Width"),
        ("camera_height", "Resolution Height"),
        ("camera_fps", "Target Framerate (FPS)"),
    ],
    "Security & Liveness": [
        ("spoof_threshold", "Spoof Rejection Threshold (0.0 - 1.0)"),
        ("challenge_timeout", "Liveness Challenge Timeout (seconds)"),
    ],
    "Face Detection Engine": [
        ("min_face_size", "Minimum Face Size (pixels)"),
    ]
}

class FormField(Vertical):
    """Wrapper for Input with an associated validation label."""
    
    DEFAULT_CSS = """
    FormField {
        height: auto;
        margin-bottom: 1;
    }
    .field-label {
        color: #94a3b8;
        margin-bottom: 1;
    }
    .field-error {
        color: #ff3366;
        height: 1;
    }
    """

    class Changed(Message):
        def __init__(self, field_id: str, is_valid: bool) -> None:
            self.field_id = field_id
            self.is_valid = is_valid
            super().__init__()

    def __init__(self, field_id: str, label_text: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.field_id = field_id
        self.label_text = label_text
        self.is_valid = True

    def compose(self) -> ComposeResult:
        yield Label(self.label_text, classes="field-label")
        yield Input(id=self.field_id)
        yield Label("", id=f"{self.field_id}-error", classes="field-error")

    def get_value(self) -> str:
        return self.query_one(Input).value

    def set_value(self, value: Any) -> None:
        inp = self.query_one(Input)
        inp.value = str(value)
        self.validate()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.validate()
        
    def validate(self) -> None:
        val = self.query_one(Input).value
        valid, msg, _ = ConfigSchema.validate(self.field_id, val)
        
        err_lbl = self.query_one(f"#{self.field_id}-error", Label)
        inp = self.query_one(Input)
        
        self.is_valid = valid
        
        if not valid:
            err_lbl.update(msg)
            inp.add_class("-invalid")
        else:
            err_lbl.update("")
            inp.remove_class("-invalid")
            
        self.post_message(self.Changed(self.field_id, valid))


class SettingsScreen(Screen):
    """
    Form-based configuration editor reading and writing to the daemon over IPC.
    """

    DEFAULT_CSS = """
    SettingsScreen {
        layout: vertical;
    }
    #settings-container {
        padding: 1 2;
        overflow-y: hidden;
        height: 1fr;
    }
    .settings-header {
        layout: horizontal;
        height: 3;
        align: left middle;
        margin-bottom: 1;
        border-bottom: solid #1e3a5f;
    }
    .settings-title {
        width: 1fr;
        color: #00d4ff;
        text-style: bold;
    }
    #version-badge {
        width: auto;
        padding: 0 1;
        margin-right: 2;
        background: #1e293b;
        color: #94a3b8;
        border: round #1e3a5f;
    }
    #upgrade-notice {
        height: auto;
        background: #4a3600;
        border: round #ffaa00;
        color: #ffaa00;
        padding: 1 2;
        margin-bottom: 1;
        display: none;
    }
    .form-group-title {
        color: #e2e8f0;
        text-style: bold;
        padding: 1 0;
        border-bottom: solid #1e293b;
        margin: 1 0;
    }
    .form-actions {
        height: 3;
        layout: horizontal;
        margin-top: 2;
        align: left middle;
    }
    .form-actions > * {
        margin-right: 2;
    }
    """

    def __init__(self, ipc: SentinelIPCClient, **kwargs) -> None:
        super().__init__(**kwargs)
        self._ipc = ipc
        self._has_unsaved_changes = False
        self._daemon_config_version = 1

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-container"):
            # Header
            with Horizontal(classes="settings-header"):
                yield Label("System Configuration", classes="settings-title")
                yield Label("Loading...", id="version-badge")

            yield Label("Configuration format requires upgrade.", id="upgrade-notice")

            # Form Area (Scrollable)
            with VerticalScroll(id="form-scroll"):
                for section_title, fields in FORM_SECTIONS.items():
                    yield Label(section_title, classes="form-group-title")
                    for f_id, f_label in fields:
                        yield FormField(f_id, f_label, id=f"field-{f_id}")

                # Action Bar
                with Horizontal(classes="form-actions"):
                    yield Button("Save Changes", variant="primary", id="btn-save", disabled=True)
                    yield Button("Reload", id="btn-reload")
                    yield Button("Reset to Defaults", variant="error", id="btn-reset")

    def on_mount(self) -> None:
        self._load_config()

    def _load_config(self) -> None:
        self.notify("Loading configuration from daemon...")
        self.run_worker(self._do_fetch_config, thread=True)

    def _do_fetch_config(self) -> None:
        res = self._ipc.call("get_config", timeout=IPC_READ_TIMEOUT)
        self.call_from_thread(self._populate_form, res)

    def _populate_form(self, result: dict) -> None:
        if not result.get("success"):
            code = result.get("error_code", "UNKNOWN")
            self.notify(f"Failed to load config: {code}", severity="error")
            return

        cfg = result.get("config", {})
        
        # Determine config version & badge
        self._daemon_config_version = cfg.get("config_version", 1)
        badge = self.query_one("#version-badge", Label)
        badge.update(f"Config v{self._daemon_config_version}")
        
        ok, msg = ConfigSchema.check_version(self._daemon_config_version)
        notice = self.query_one("#upgrade-notice", Label)
        if not ok:
            notice.update(msg)
            notice.styles.display = "block"
        else:
            notice.styles.display = "none"

        # Populate Fields
        try:
            for section_title, fields in FORM_SECTIONS.items():
                for f_id, _ in fields:
                    if f_id in cfg:
                        # Find the FormField and set value without triggering unsaved changes logic yet
                        ff = self.query_one(f"#field-{f_id}", FormField)
                        ff.set_value(cfg[f_id])
            
            self._has_unsaved_changes = False
            self.query_one("#btn-save", Button).disabled = True
            self.notify("Configuration loaded.")
        except Exception as e:
            logger.error(f"Error populating config form: {e}")

    def on_form_field_changed(self, message: FormField.Changed) -> None:
        """Triggered whenever any input changes its validation state."""
        self._has_unsaved_changes = True
        
        # Check if all fields are valid
        all_valid = True
        for section_title, fields in FORM_SECTIONS.items():
            for f_id, _ in fields:
                try:
                    ff = self.query_one(f"#field-{f_id}", FormField)
                    if not ff.is_valid:
                        all_valid = False
                except Exception:
                    pass
        
        self.query_one("#btn-save", Button).disabled = not all_valid

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        
        if button_id == "btn-reload":
            self._load_config()
            
        elif button_id == "btn-save":
            self._save_config()
            
        elif button_id == "btn-reset":
            # Real dialogs come later (Phase 8 generally), we trigger directly for now
            self.notify("Resetting daemon config to defaults...")
            self.run_worker(self._do_reset_config, thread=True)

    def _save_config(self) -> None:
        # Scrape form values
        form_data = {}
        for section_title, fields in FORM_SECTIONS.items():
            for f_id, _ in fields:
                try:
                    ff = self.query_one(f"#field-{f_id}", FormField)
                    form_data[f_id] = ff.get_value()
                except Exception:
                    pass
                    
        # Apply validation limits / casting
        rpc_payload = ConfigSchema.to_rpc_format(form_data)
        
        self.notify("Saving configuration...")
        self.run_worker(lambda: self._do_save(rpc_payload), thread=True)

    def _do_save(self, payload: dict) -> None:
        res = self._ipc.call("update_config", {"config": payload}, timeout=IPC_READ_TIMEOUT)
        self.call_from_thread(self._save_complete, res)
        
    def _save_complete(self, result: dict) -> None:
        if result.get("success"):
            self.notify("Configuration saved successfully.", severity="info")
            self._load_config()  # Refresh form state fully
        else:
            self.notify(f"Failed to save: {result.get('error_code', 'Unknown Error')}", severity="error")

    def _do_reset_config(self) -> None:
        res = self._ipc.call("reset_config", timeout=IPC_READ_TIMEOUT)
        self.call_from_thread(self._reset_complete, res)
        
    def _reset_complete(self, result: dict) -> None:
        if result.get("success"):
            self.notify("Configuration reset. Reloading...", severity="info")
            self._load_config()
        else:
            self.notify("Reset failed.", severity="error")
