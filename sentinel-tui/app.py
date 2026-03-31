"""
app.py — Main Textual application for Project Sentinel TUI.

Entry point: main() — called by `sentinel-tui` script and `python -m sentinel_tui`.

Features:
  - CLI flags: --socket, --debug, --version
  - SIGINT/SIGTERM graceful shutdown
  - Sidebar navigation with keyboard shortcuts
  - Global IPC client shared across all screens
  - ConnectionStatus widget in header
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from sentinel_tui.constants import (
    APP_SUBTITLE,
    APP_TITLE,
    APP_VERSION,
    DEFAULT_SOCKET_PATH,
    PROTOCOL_VERSION,
)
from sentinel_tui.services.ipc_client import SentinelIPCClient
from sentinel_tui.widgets.connection_status import ConnectionStatus

logger = logging.getLogger(__name__)

# Sidebar navigation items: (icon, label, screen_id)
NAV_ITEMS = [
    ("󰋊", "Dashboard",      "dashboard"),
    ("󰟍", "Enrollment",     "enrollment"),
    ("󰯃", "Authentication", "authentication"),
    ("󰄬", "Devices",        "devices"),
    ("󰒓", "Settings",       "settings"),
    ("󰙬", "Diagnostics",    "diagnostics"),
]


class SentinelApp(App):
    """
    Project Sentinel Terminal Control Interface.

    Keyboard shortcuts:
      d  → Dashboard
      e  → Enrollment
      a  → Authentication
      v  → Devices
      s  → Settings
      x  → Diagnostics
      q  → Quit
    """

    TITLE = APP_TITLE
    CSS_PATH = "styles/app.tcss"

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("d", "show_screen('dashboard')",      "Dashboard",      show=True),
        Binding("e", "show_screen('enrollment')",     "Enrollment",     show=True),
        Binding("a", "show_screen('authentication')", "Auth",           show=True),
        Binding("v", "show_screen('devices')",        "Devices",        show=True),
        Binding("s", "show_screen('settings')",       "Settings",       show=True),
        Binding("x", "show_screen('diagnostics')",    "Diagnostics",    show=True),
        Binding("q", "quit",                          "Quit",           show=True),
        Binding("?", "show_help",                     "Help",           show=False),
    ]

    def __init__(self, socket_path: str, debug: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self._socket_path = socket_path
        self._debug       = debug
        self._ipc         = SentinelIPCClient(socket_path, debug=debug)
        self._active_nav  = "dashboard"

        if debug:
            logging.basicConfig(
                level=logging.DEBUG,
                format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
                stream=sys.stderr,
            )
            logger.debug(f"Debug mode active. Socket: {socket_path}")

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        """App is mounted — register signal handlers and connect IPC."""
        # Register graceful shutdown signal handlers
        signal.signal(signal.SIGINT,  self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        # Set socket path on connection status widget
        try:
            self.query_one(ConnectionStatus).set_socket_path(self._socket_path)
        except Exception:
            pass

        # Kick off IPC connection in background thread (non-blocking)
        self.run_worker(self._connect_ipc, thread=True)

        # Load dashboard as default screen
        self.action_show_screen("dashboard")

    def on_unmount(self) -> None:
        """App is shutting down — clean up IPC and any subprocesses."""
        try:
            self._ipc.disconnect()
        except Exception:
            pass

    def _handle_signal(self, sig: int, frame) -> None:  # type: ignore[type-arg]
        """SIGINT / SIGTERM handler — triggers clean Textual shutdown."""
        logger.info(f"Signal {sig} received — shutting down TUI.")
        self.call_from_thread(self.exit)

    def _connect_ipc(self) -> None:
        """Background worker: establish IPC connection."""
        success = self._ipc.connect()
        if success:
            logger.debug("IPC connection established.")
        else:
            logger.warning(f"IPC connection failed: {self._ipc._connect_error}")

    # ── Composition ────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield self._build_header()
        yield self._build_body()
        yield Footer()

    def _build_header(self) -> Static:
        from textual.app import ComposeResult as CR
        from textual.widget import Widget

        class AppHeader(Widget):
            DEFAULT_CSS = """
            AppHeader {
                height: 3;
                background: #0f1729;
                border-bottom: heavy #1e3a5f;
                layout: horizontal;
                align: left middle;
                padding: 0 2;
            }
            """
            def compose(self) -> CR:
                yield Label(
                    f"🛡  {APP_TITLE}",
                    id="header-title",
                    classes="label--cyan",
                )
                yield Label(APP_SUBTITLE, id="header-subtitle", classes="label--muted")
                yield ConnectionStatus(id="conn-status")

        return AppHeader()

    def _build_body(self) -> Static:
        from textual.widget import Widget
        from textual.app import ComposeResult as CR

        app = self

        class AppBody(Widget):
            DEFAULT_CSS = """
            AppBody {
                layout: horizontal;
                height: 1fr;
            }
            """
            def compose(self) -> CR:
                yield AppSidebar()
                yield ContentArea()

        class AppSidebar(Widget):
            DEFAULT_CSS = """
            AppSidebar {
                width: 22;
                background: #111827;
                border-right: heavy #1e3a5f;
                padding: 1 0;
            }
            """
            def compose(self) -> CR:
                yield Label("NAVIGATION", id="sidebar-title", classes="label--muted")
                items = []
                for icon, label, screen_id in NAV_ITEMS:
                    item = ListItem(
                        Label(f" {icon}  {label}", classes="nav-label"),
                        id=f"nav-{screen_id}",
                    )
                    items.append(item)
                yield ListView(*items, id="nav-list")

        class ContentArea(Widget):
            DEFAULT_CSS = """
            ContentArea {
                width: 1fr;
                height: 1fr;
            }
            """
            def compose(self) -> CR:
                # Content injected via screen switching
                yield Label(
                    "Loading...",
                    id="placeholder",
                    classes="label--muted",
                )

        return AppBody()

    # ── Navigation Actions ─────────────────────────────────────────────────────

    def action_show_screen(self, screen_id: str) -> None:
        """Switch to the named screen."""
        self._active_nav = screen_id
        self._highlight_nav(screen_id)

        try:
            content = self.query_one("ContentArea")  # type: ignore[arg-type]
            content.remove_children()
        except Exception:
            pass

        screen = self._load_screen(screen_id)
        if screen:
            try:
                content = self.query_one("ContentArea")  # type: ignore[arg-type]
                content.mount(screen)
            except Exception as exc:
                logger.error(f"Failed to mount screen '{screen_id}': {exc}")

    def _highlight_nav(self, active_id: str) -> None:
        """Update sidebar highlighting."""
        try:
            nav_list = self.query_one("#nav-list", ListView)
            for item in nav_list.query(ListItem):
                item.remove_class("--active")
            active = self.query_one(f"#nav-{active_id}", ListItem)
            active.add_class("--active")
        except Exception:
            pass

    def _load_screen(self, screen_id: str):
        """Lazy-import and instantiate a screen by ID."""
        try:
            if screen_id == "dashboard":
                from sentinel_tui.screens.dashboard import DashboardScreen
                return DashboardScreen(self._ipc, debug=self._debug)
            elif screen_id == "enrollment":
                from sentinel_tui.screens.enrollment import EnrollmentScreen
                return EnrollmentScreen(self._ipc)
            elif screen_id == "authentication":
                from sentinel_tui.screens.authentication import AuthenticationScreen
                return AuthenticationScreen(self._ipc)
            elif screen_id == "devices":
                from sentinel_tui.screens.devices import DevicesScreen
                return DevicesScreen(self._ipc)
            elif screen_id == "settings":
                from sentinel_tui.screens.settings import SettingsScreen
                return SettingsScreen(self._ipc)
            elif screen_id == "diagnostics":
                from sentinel_tui.screens.diagnostics import DiagnosticsScreen
                return DiagnosticsScreen(self._ipc)
        except ImportError:
            from textual.widgets import Static
            return Static(
                f"[dim]Screen '{screen_id}' not yet implemented (coming in a future phase)[/dim]",
                id=f"placeholder-{screen_id}",
            )
        return None

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Sidebar item clicked."""
        item_id = event.item.id or ""
        if item_id.startswith("nav-"):
            screen_id = item_id[4:]
            self.action_show_screen(screen_id)

    def action_show_help(self) -> None:
        self.notify(
            "Keyboard: d=Dashboard  e=Enrollment  a=Auth  v=Devices  s=Settings  x=Diagnostics  q=Quit",
            title="Keyboard Shortcuts",
            timeout=6,
        )

    def action_quit(self) -> None:
        self._ipc.disconnect()
        self.exit()


# ── CLI Entry Point ────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sentinel-tui",
        description=f"{APP_TITLE} — Terminal Control Interface",
    )
    parser.add_argument(
        "--socket",
        default=DEFAULT_SOCKET_PATH,
        metavar="PATH",
        help=f"Override Unix socket path (default: {DEFAULT_SOCKET_PATH})",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logging and show raw IPC messages",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"sentinel-tui {APP_VERSION} (protocol v{PROTOCOL_VERSION})",
    )
    return parser.parse_args()


def main() -> None:
    """Main entry point — called by `sentinel-tui` script."""
    args = _parse_args()

    app = SentinelApp(
        socket_path=args.socket,
        debug=args.debug,
    )
    app.run()


if __name__ == "__main__":
    main()
