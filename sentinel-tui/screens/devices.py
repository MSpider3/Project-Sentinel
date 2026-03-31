"""
devices.py — Hardware and Camera Device Manager.

Lists available `/dev/video*` devices, triggers a daemon-side capabilities probe,
and allows the user to switch the active camera dynamically.
"""

from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Label, ListItem, ListView, Static

from sentinel_tui.constants import IPC_PREVIEW_TIMEOUT, ErrorCode
from sentinel_tui.services.ipc_client import SentinelIPCClient

logger = logging.getLogger(__name__)


class DeviceCard(ListItem):
    """Visual representation of a single camera device."""
    
    DEFAULT_CSS = """
    DeviceCard {
        background: #111827;
        border: round #1e3a5f;
        padding: 1 2;
        margin-bottom: 1;
        layout: vertical;
        height: auto;
    }
    DeviceCard:focus {
        border: round #00d4ff;
    }
    DeviceCard.--active-device {
        border: round #00ff88;
        background: #0e4a2a;
    }
    .dev-header {
        layout: horizontal;
        height: 1;
        margin-bottom: 1;
    }
    .dev-index { color: #00d4ff; text-style: bold; width: 14; }
    .dev-name { color: #e2e8f0; width: 1fr; }
    .dev-status { width: auto; text-style: bold; color: #4b5563; }
    .dev-caps { color: #94a3b8; }
    """

    def __init__(self, idx: int, name: str, is_active: bool, caps: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.device_index = idx
        self._name_str = name
        self._is_active = is_active
        self._caps_str = caps

    def compose(self) -> ComposeResult:
        if self._is_active:
            self.add_class("--active-device")
            
        with Horizontal(classes="dev-header"):
            yield Label(f"/dev/video{self.device_index}", classes="dev-index")
            yield Label(self._name_str, classes="dev-name")
            if self._is_active:
                yield Label("ACTIVE", classes="dev-status", style="color: #00ff88;")

        yield Label(f"Caps: {self._caps_str}", classes="dev-caps")


class DevicesScreen(Screen):
    """
    Hardware diagnostic screen.
    Queries daemon for v4l2 devices.
    """

    DEFAULT_CSS = """
    DevicesScreen {
        layout: vertical;
    }
    #dev-container {
        padding: 1 2;
        height: 1fr;
    }
    .dev-title {
        color: #00d4ff;
        text-style: bold;
        border-bottom: solid #1e3a5f;
        padding-bottom: 1;
        margin-bottom: 2;
    }
    .dev-actions {
        height: 3;
        layout: horizontal;
        margin-bottom: 1;
    }
    .dev-actions > Button { margin-right: 1; }
    #dev-list {
        height: 1fr;
        background: transparent;
    }
    #dev-loading {
        color: #ffaa00;
        margin: 2;
    }
    """

    def __init__(self, ipc: SentinelIPCClient, **kwargs) -> None:
        super().__init__(**kwargs)
        self._ipc = ipc

    def compose(self) -> ComposeResult:
        with Container(id="dev-container"):
            yield Label("Camera Device Manager", classes="dev-title")
            
            with Horizontal(classes="dev-actions"):
                yield Button("Scan Devices", id="btn-scan", variant="primary")
                yield Button("Set Selected as Active", id="btn-set-active", variant="success", disabled=True)
            
            yield Label("Loading devices...", id="dev-loading")
            yield ListView(id="dev-list")

    def on_mount(self) -> None:
        self._scan_devices()

    def _scan_devices(self) -> None:
        self.query_one("#dev-loading", Label).styles.display = "block"
        self.query_one("#dev-list", ListView).clear()
        self.query_one("#btn-set-active", Button).disabled = True
        self.run_worker(self._do_scan, thread=True)

    def _do_scan(self) -> None:
        res = self._ipc.call("get_devices", timeout=IPC_PREVIEW_TIMEOUT)
        self.call_from_thread(self._populate, res)

    def _populate(self, result: dict) -> None:
        self.query_one("#dev-loading", Label).styles.display = "none"
        lst = self.query_one("#dev-list", ListView)
        
        if not result.get("success"):
            code = result.get("error_code", "UNKNOWN")
            self.notify(f"Scan failed: {ErrorCode.describe(code)}", severity="error")
            lst.append(ListItem(Label("Failed to read devices.")))
            return
            
        devices = result.get("devices", [])
        active_idx = result.get("active_device", -1)
        
        if not devices:
            lst.append(ListItem(Label("No V4L2 camera devices found.")))
            return
            
        for dev in devices:
            idx = dev.get("index", 0)
            name = dev.get("name", "Unknown Camera")
            caps = dev.get("caps", "N/A")
            is_active = (idx == active_idx)
            
            card = DeviceCard(idx, name, is_active, caps)
            lst.append(card)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, DeviceCard):
            self.query_one("#btn-set-active", Button).disabled = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-scan":
            self._scan_devices()
        elif event.button.id == "btn-set-active":
            self._set_active_device()

    def _set_active_device(self) -> None:
        lst = self.query_one("#dev-list", ListView)
        if hasattr(lst, "highlighted_child") and isinstance(lst.highlighted_child, DeviceCard):
            idx = lst.highlighted_child.device_index
            self.notify(f"Setting /dev/video{idx} as active camera...")
            self.run_worker(lambda: self._do_set_active(idx), thread=True)

    def _do_set_active(self, idx: int) -> None:
        # We reuse update_config to set the device id
        payload = {"camera_device_id": idx}
        res = self._ipc.call("update_config", {"config": payload})
        
        def _handle():
            if res.get("success"):
                self.notify(f"Camera {idx} is now active.", severity="info")
                self._scan_devices()
            else:
                self.notify("Failed to change camera.", severity="error")
                
        self.app.call_from_thread(_handle)
