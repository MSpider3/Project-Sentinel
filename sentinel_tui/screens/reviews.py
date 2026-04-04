"""
reviews.py — Intrusion review and blacklist management screen.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Label, ListItem, ListView, Static

from sentinel_tui.constants import IPC_READ_TIMEOUT, ErrorCode
from sentinel_tui.services.ipc_client import SentinelIPCClient

logger = logging.getLogger(__name__)

class IntrusionItem(ListItem):
    """A single intrusion entry in the list."""
    def __init__(self, filepath: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.filepath = filepath
        # Filename format: intrusion_20240402_203158.jpg
        basename = os.path.basename(filepath)
        try:
            parts = basename.replace(".jpg", "").split("_")
            if len(parts) >= 3:
                date_str = parts[1]
                time_str = parts[2]
                self.timestamp = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]} {time_str[:2]}:{time_str[2:4]}:{time_str[4:]}"
            else:
                self.timestamp = basename
        except Exception:
            self.timestamp = basename

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Label(f"󰄱  {self.timestamp}", classes="item-ts")
            yield Label(os.path.basename(self.filepath), classes="item-path label--muted")
            with Horizontal(classes="item-actions"):
                yield Button("View", id="btn-view", variant="primary")
                yield Button("Delete", id="btn-delete", variant="error")

class ReviewsScreen(Container):
    """
    Intrusion Review Screen.
    Allows user to see blacklisted attempts and delete false positives.
    """

    DEFAULT_CSS = """
    ReviewsScreen {
        layout: vertical;
        padding: 1 2;
    }
    #reviews-list {
        background: #0a0e1a;
        border: round #1e3a5f;
        margin: 1 0;
        height: 1fr;
    }
    IntrusionItem {
        height: 3;
        padding: 0 2;
        border-bottom: solid #1e3a5f;
        layout: horizontal;
        align: left middle;
    }
    .item-ts { width: 22; text-style: bold; color: #00d4ff; }
    .item-path { width: 1fr; }
    .item-actions { width: auto; align: right middle; }
    .item-actions Button { margin-left: 1; min-width: 8; height: 1; border: none; }
    """

    def __init__(self, ipc: SentinelIPCClient, **kwargs) -> None:
        super().__init__(**kwargs)
        self._ipc = ipc

    def compose(self) -> ComposeResult:
        yield Label("Security Intrusion Reviews", classes="section-header")
        yield Label("The following attempts were blacklisted for spoofing or unauthorized access.", classes="label--muted")
        
        yield ListView(id="reviews-list")
        
        with Horizontal(classes="footer-actions"):
            yield Button("Refresh List", id="btn-refresh")
            yield Label("Select an item to review or delete from blacklist.", classes="label--muted")

    def on_mount(self) -> None:
        self._refresh_list()

    def _refresh_list(self) -> None:
        self.run_worker(self._do_fetch_intrusions, thread=True)

    def _do_fetch_intrusions(self) -> None:
        res = self._ipc.call("get_intrusions", timeout=IPC_READ_TIMEOUT)
        
        def _update():
            lst = self.query_one("#reviews-list", ListView)
            lst.clear()
            
            if not res.get("success"):
                self.notify("Failed to fetch intrusions", severity="error")
                return
            
            files = res.get("files", [])
            if not files:
                self.notify("No intrusions found to review.", title="Clean Record")
            
            for f in files:
                lst.append(IntrusionItem(f))
        
        self.app.call_from_thread(_update)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        # Find the parent IntrusionItem
        item = event.button.parent
        while item and not isinstance(item, IntrusionItem):
            item = item.parent
        
        if not item: 
            if event.button.id == "btn-refresh":
                self._refresh_list()
            return

        if event.button.id == "btn-view":
            self._view_image(item.filepath)
        elif event.button.id == "btn-delete":
            self._delete_intrusion(item.filepath)

    def _view_image(self, path: str) -> None:
        """Open the image while handling root mount restrictions.
        
        For browser viewers (xdg-open/gio), copy to temp location since blacklist
        dir may not be accessible from user's browser due to mount restrictions.
        """
        try:
            import pwd
            import tempfile
            import shutil as sh_util
            
            # Check if running as root
            is_root = os.getuid() == 0
            
            # Prefer a lightweight image viewer to avoid Chromium sandbox root errors.
            # Common options: eog (Eye of GNOME), feh, sxiv - these work directly with blacklist path.
            viewers = ["eog", "feh", "sxiv"]
            for v in viewers:
                if shutil.which(v):
                    # If root, try to run as the actual user if possible
                    if is_root:
                        try:
                            # Find the user running sudo
                            sudo_user = os.environ.get("SUDO_USER")
                            if sudo_user:
                                user_info = pwd.getpwnam(sudo_user)
                                uid = user_info.pw_uid
                                subprocess.Popen(
                                    ["runuser", "-u", sudo_user, "--", v, path],
                                    start_new_session=True
                                )
                                self.notify(f"Opening {os.path.basename(path)} with {v}...", title="Review")
                                return
                        except Exception:
                            pass  # Fall through to running as root
                    
                    # Run directly as root (acceptable for native viewers)
                    subprocess.Popen([v, path], start_new_session=True)
                    self.notify(f"Opening {os.path.basename(path)} with {v}...", title="Review")
                    return

            # Fallback: Use xdg-open/gio open (browsers) - needs temp copy for mount accessibility
            # Copy to temp location that user can access (some mounts restrict root dirs)
            try:
                # Get temp directory
                if is_root:
                    sudo_user = os.environ.get("SUDO_USER")
                    if sudo_user:
                        user_info = pwd.getpwnam(sudo_user)
                        uid = user_info.pw_uid
                        temp_dir = f"/run/user/{uid}"
                    else:
                        temp_dir = "/tmp"
                else:
                    temp_dir = "/tmp"
                
                # Create temp directory if needed
                os.makedirs(temp_dir, exist_ok=True)
                
                # Copy image to temp location
                temp_path = os.path.join(temp_dir, f"intrusion_view_{os.getpid()}.jpg")
                sh_util.copy2(path, temp_path)
                os.chmod(temp_path, 0o644)  # Ensure readable
                
                # Now open the temp copy
                if shutil.which("gio"):
                    cmd = ["gio", "open", temp_path]
                    if is_root:
                        try:
                            sudo_user = os.environ.get("SUDO_USER")
                            if sudo_user:
                                cmd = ["runuser", "-u", sudo_user, "--"] + cmd
                        except Exception:
                            pass
                    subprocess.Popen(cmd, start_new_session=True)
                else:
                    cmd = ["xdg-open", temp_path]
                    if is_root:
                        try:
                            sudo_user = os.environ.get("SUDO_USER")
                            if sudo_user:
                                cmd = ["runuser", "-u", sudo_user, "--"] + cmd
                        except Exception:
                            pass
                    subprocess.Popen(cmd, start_new_session=True)

                self.notify(f"Opening {os.path.basename(path)}...", title="Review")
                
                # Clean up temp file after a delay
                def cleanup():
                    import time
                    time.sleep(30)  # Wait 30s for browser to load
                    try:
                        if os.path.exists(temp_path):
                            os.remove(temp_path)
                    except Exception:
                        pass
                threading.Thread(target=cleanup, daemon=True).start()
                
            except Exception as copy_err:
                self.notify(f"Could not copy image: {copy_err}", severity="error")
                
        except Exception as e:
            self.notify(f"Could not open image: {e}", severity="error")

    def _delete_intrusion(self, path: str) -> None:
        """Tell the daemon to delete the intrusion record."""
        def _do_delete():
            res = self._ipc.call("delete_intrusion", {"filename": path})
            if res.get("success"):
                self.app.call_from_thread(self._refresh_list)
                self.app.call_from_thread(self.notify, "Intrusion record deleted.", title="Blacklist Updated")
            else:
                self.app.call_from_thread(self.notify, "Failed to delete record.", severity="error")
        
        self.run_worker(_do_delete, thread=True)
