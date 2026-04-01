"""
connection_status.py — Header bar widget showing daemon connection health.

Auto-pings the daemon via the `health` RPC every CONNECTION_PING_INTERVAL seconds.
Displays retry count during reconnection and shows the socket path on click.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label
from rich.markup import escape as markup_escape

from sentinel_tui.constants import (
    CONNECTION_PING_INTERVAL,
    IPC_MAX_RETRIES,
    IPC_READ_TIMEOUT,
    ErrorCode,
)


class ConnectionStatus(Widget):
    """
    Compact connection indicator for the app header.

    States:
      connected   → ● Connected  (green)
      retrying    → ● Retrying (N/5)  (amber)
      disconnected → ● Disconnected — Max retries reached  (red)
      connecting  → ● Connecting...  (dim)
    """

    DEFAULT_CSS = """
    ConnectionStatus {
        height: 1;
        width: auto;
        layout: horizontal;
        padding: 0 1;
    }
    ConnectionStatus .conn-dot { width: 2; }
    ConnectionStatus .conn-dot--connected    { color: #00ff88; }
    ConnectionStatus .conn-dot--retrying     { color: #ffaa00; }
    ConnectionStatus .conn-dot--disconnected { color: #ff3366; }
    ConnectionStatus .conn-dot--connecting   { color: #4b5563; }
    ConnectionStatus .conn-label { color: #94a3b8; }
    ConnectionStatus .conn-socket { color: #4b5563; margin-left: 1; }
    """

    _state: reactive[str] = reactive("connecting")
    _retry_n: reactive[int] = reactive(0)

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._socket_path: str = "—"

    def compose(self) -> ComposeResult:
        yield Label("●", classes="conn-dot conn-dot--connecting", id="conn-dot")
        yield Label("Connecting...", classes="conn-label", id="conn-label")
        yield Label("", classes="conn-socket", id="conn-socket")

    def on_mount(self) -> None:
        self.set_interval(CONNECTION_PING_INTERVAL, self._ping)

    def set_socket_path(self, path: str) -> None:
        self._socket_path = path
        try:
            # Safely escape the brackets and path so Rich doesn't treat them as a markup tag
            safe_text = markup_escape(f"[{path}]")
            self.query_one("#conn-socket", Label).update(safe_text)
        except Exception:
            pass

    def _ping(self) -> None:
        """Called on interval — ping daemon and update display."""
        self.run_worker(self._do_ping, exclusive=True, thread=True)

    def _do_ping(self) -> None:
        """Worker: runs in thread, calls health endpoint, updates widget."""
        try:
            # Access IPC client from the app
            client = self.app._ipc  # type: ignore[attr-defined]
            result = client.call("health", timeout=IPC_READ_TIMEOUT)

            if result.get("success") is False:
                code = result.get("error_code", ErrorCode.DAEMON_NOT_RUNNING)
                if code == ErrorCode.DAEMON_NOT_RUNNING and client.is_exhausted():
                    self._set_state("disconnected", client.get_retry_count())
                else:
                    self._set_state("retrying", client.get_retry_count())
            else:
                self._set_state("connected", 0)
        except Exception:
            self._set_state("retrying", 0)

    def _set_state(self, state: str, retry_n: int) -> None:
        """Update DOM from worker thread via call_from_thread."""
        self.app.call_from_thread(self._update_dom, state, retry_n)

    def _update_dom(self, state: str, retry_n: int) -> None:
        """Must run on main thread (Textual DOM thread)."""
        self._state  = state
        self._retry_n = retry_n

        dot_classes = {
            "connected":    "conn-dot--connected",
            "retrying":     "conn-dot--retrying",
            "disconnected": "conn-dot--disconnected",
            "connecting":   "conn-dot--connecting",
        }

        labels = {
            "connected":    "Connected",
            "retrying":     f"Retrying ({retry_n}/{IPC_MAX_RETRIES})...",
            "disconnected": "Disconnected — Max retries reached",
            "connecting":   "Connecting...",
        }

        try:
            dot = self.query_one("#conn-dot", Label)
            # Clear old state classes
            for cls in dot_classes.values():
                dot.remove_class(cls)
            dot.add_class(dot_classes.get(state, "conn-dot--connecting"))

            lbl = self.query_one("#conn-label", Label)
            lbl.update(labels.get(state, "Unknown"))
        except Exception:
            pass
