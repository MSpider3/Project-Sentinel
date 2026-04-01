"""
diagnostics.py — Advanced system diagnostic screen for manual IPC endpoint testing.
"""

from __future__ import annotations

import json
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Input, Label, RichLog

from sentinel_tui.constants import IPC_READ_TIMEOUT
from sentinel_tui.services.ipc_client import SentinelIPCClient


class DiagnosticsScreen(Container):
    """
    Direct IPC command invoker for debugging and raw JSON interaction.
    """

    DEFAULT_CSS = """
    DiagnosticsScreen { layout: vertical; }
    #diag-container { padding: 1 2; height: 1fr; }
    .diag-title {
        color: #00d4ff; text-style: bold;
        border-bottom: solid #1e3a5f; margin-bottom: 2;
    }
    .diag-input-row { layout: horizontal; height: 3; margin-bottom: 1; }
    .diag-input-row > Input { width: 1fr; margin-right: 1; }
    .diag-input-row > Button { width: auto; }
    #diag-log { height: 1fr; border: solid #1e3a5f; background: #000; }
    """

    def __init__(self, ipc: SentinelIPCClient, **kwargs) -> None:
        super().__init__(**kwargs)
        self._ipc = ipc

    def compose(self) -> ComposeResult:
        with Container(id="diag-container"):
            yield Label("System Diagnostics — IPC Shell", classes="diag-title")
            
            with Horizontal(classes="diag-input-row"):
                yield Input(placeholder="RPC Method (e.g., ping, health)", id="input-method", value="ping")
                yield Input(placeholder='JSON Params (optional, e.g. {"timeout": 5})', id="input-params")
                yield Button("Send", id="btn-send", variant="primary")
                yield Button("Clear", id="btn-clear")

            yield RichLog(id="diag-log", markup=True, highlight=True, auto_scroll=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-send":
            self._send_rpc()
        elif event.button.id == "btn-clear":
            self.query_one("#diag-log", RichLog).clear()

    def _send_rpc(self) -> None:
        method = self.query_one("#input-method", Input).value.strip()
        params_str = self.query_one("#input-params", Input).value.strip()
        
        if not method: return
        
        params = {}
        if params_str:
            try:
                params = json.loads(params_str)
            except json.JSONDecodeError as e:
                self._log(f"[red]Error parsing params JSON:[/red] {e}")
                return
                
        self._log(f"\n[cyan]>[/cyan] {method} {params_str or '{}'}")
        self.run_worker(lambda: self._do_rpc(method, params), thread=True)

    def _do_rpc(self, method: str, params: dict) -> None:
        res = self._ipc.call(method, params, timeout=IPC_READ_TIMEOUT)
        
        def _update():
            formatted = json.dumps(res, indent=2)
            color = "green" if res.get("success") else "red"
            lbl = "Result" if res.get("success") else "Error"
            
            tag = f"[{color}]{lbl}[/{color}]"
            self._log(f"{tag}\n{formatted}")
            
        self.app.call_from_thread(_update)

    def _log(self, text: str) -> None:
        self.query_one("#diag-log", RichLog).write(text)
