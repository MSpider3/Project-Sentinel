"""
ipc_client.py — Versioned JSON-RPC Unix socket client for Project Sentinel TUI.

Design principles:
  - Injects protocol_version into every request automatically
  - Per-method configurable timeouts (camera ops need longer timeouts)
  - Exponential backoff reconnection with hard retry limit (no infinite loops)
  - Thread-safe via Lock (Textual workers run in threads)
  - All errors return structured dicts with error_code — never raise to callers
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from typing import Any

from sentinel_tui.constants import (
    ErrorCode,
    IPC_CONNECT_TIMEOUT,
    IPC_MAX_RETRIES,
    IPC_READ_TIMEOUT,
    IPC_RETRY_BASE_WAIT,
    IPC_RETRY_MAX_WAIT,
    PROTOCOL_VERSION,
)

logger = logging.getLogger(__name__)


class SentinelIPCClient:
    """
    Thread-safe JSON-RPC client over a Unix domain socket.

    Usage:
        client = SentinelIPCClient("/run/sentinel/sentinel.sock")
        client.connect()
        result = client.call("ping")
        client.disconnect()
    """

    def __init__(self, socket_path: str, debug: bool = False) -> None:
        self._socket_path   = socket_path
        self._debug         = debug
        self._sock: socket.socket | None = None
        self._lock          = threading.Lock()
        self._request_id    = 0
        self._connected     = False
        self._retry_count   = 0
        self._exhausted     = False   # True after MAX_RETRIES failed
        self._connect_error: str | None = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """
        Establish connection to the daemon socket.
        Returns True on success, False on failure.
        Sets self._connect_error with a human-readable message on failure.
        """
        with self._lock:
            if self._connected:
                return True
            return self._do_connect()

    def disconnect(self) -> None:
        """Clean shutdown — closes socket and resets state."""
        with self._lock:
            self._close_socket()
            self._connected  = False
            self._exhausted  = False
            self._retry_count = 0
        logger.debug("IPC client disconnected.")

    def is_connected(self) -> bool:
        """Return current connection state (non-blocking)."""
        return self._connected and not self._exhausted

    def is_exhausted(self) -> bool:
        """True when max retries have been hit — requires manual reconnect."""
        return self._exhausted

    def get_retry_count(self) -> int:
        return self._retry_count

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = IPC_READ_TIMEOUT,
    ) -> dict[str, Any]:
        """
        Send a JSON-RPC request and return the result dict.

        Always returns a dict — never raises. On any error, returns:
            {"success": False, "error_code": "<CODE>", "message": "<msg>"}

        Args:
            method:  RPC method name (e.g., "ping", "health")
            params:  Parameters dict (default: empty dict)
            timeout: Read timeout in seconds. Use constants from constants.py.
        """
        if self._exhausted:
            return self._err(ErrorCode.DAEMON_NOT_RUNNING, "Max reconnection attempts reached. Daemon unreachable.")

        if not self._connected:
            if not self.connect():
                return self._err(
                    ErrorCode.DAEMON_NOT_RUNNING,
                    self._connect_error or "Cannot connect to daemon socket.",
                )

        return self._send_request(method, params or {}, timeout)

    def reconnect(self) -> bool:
        """
        Manually trigger a reconnection attempt (resets exhausted state).
        Called by the user pressing "Retry" in the TUI.
        """
        with self._lock:
            self._exhausted   = False
            self._retry_count = 0
            self._close_socket()
            self._connected   = False
            return self._do_connect()

    # ── Internal ────────────────────────────────────────────────────────────────

    def _do_connect(self) -> bool:
        """Must be called with self._lock held."""
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(IPC_CONNECT_TIMEOUT)
            sock.connect(self._socket_path)
            sock.settimeout(None)   # switch to blocking mode after connect
            self._sock         = sock
            self._connected    = True
            self._retry_count  = 0
            self._connect_error = None
            logger.info(f"IPC connected to {self._socket_path}")
            return True
        except PermissionError:
            self._connect_error = f"Permission denied: {self._socket_path}. Is the daemon running as root?"
            logger.error(self._connect_error)
            return False
        except FileNotFoundError:
            self._connect_error = f"Socket not found: {self._socket_path}. Is the daemon running?"
            logger.error(self._connect_error)
            return False
        except OSError as exc:
            self._connect_error = f"Connection failed: {exc}"
            logger.error(self._connect_error)
            return False

    def _send_request(
        self,
        method: str,
        params: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        """Build request, send, read response. Handles reconnection on failure."""
        with self._lock:
            self._request_id += 1
            req_id = self._request_id
            request = {
                "jsonrpc": "2.0",
                "protocol_version": PROTOCOL_VERSION,
                "method": method,
                "params": params,
                "id": req_id,
            }
            payload = (json.dumps(request) + "\n").encode("utf-8")

            if self._debug:
                logger.debug(f"IPC → {payload.decode().strip()}")

            try:
                assert self._sock is not None
                self._sock.sendall(payload)

                # Read response line (newline-delimited JSON)
                self._sock.settimeout(timeout)
                raw = self._read_line(timeout)
                self._sock.settimeout(None)

                if not raw:
                    raise ConnectionError("Empty response from daemon")

                if self._debug:
                    logger.debug(f"IPC ← {raw.strip()}")

                response = json.loads(raw)
                result = response.get("result", {})

                # Check for protocol mismatch in the future
                # (daemon may add protocol_version to responses later)

                return result if isinstance(result, dict) else {"success": True, "value": result}

            except socket.timeout:
                logger.warning(f"IPC timeout on method '{method}' (>{timeout}s)")
                self._on_disconnect()
                return self._err(ErrorCode.IPC_TIMEOUT, f"Daemon did not respond within {timeout}s.")

            except (ConnectionError, BrokenPipeError, OSError) as exc:
                logger.warning(f"IPC connection lost during '{method}': {exc}")
                self._on_disconnect()
                return self._reconnect_with_backoff(method, params, timeout)

            except json.JSONDecodeError as exc:
                logger.error(f"IPC JSON parse error on '{method}': {exc}")
                return self._err(ErrorCode.UNKNOWN, f"Invalid response from daemon: {exc}")

    def _read_line(self, timeout: float) -> str:
        """Read bytes from socket until newline. Respects timeout."""
        self._sock.settimeout(timeout)
        buffer = b""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise socket.timeout("read_line deadline exceeded")
            self._sock.settimeout(remaining)
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("Daemon closed connection")
            buffer += chunk
            if b"\n" in buffer:
                line, _ = buffer.split(b"\n", 1)
                return line.decode("utf-8", errors="replace")

    def _reconnect_with_backoff(
        self,
        method: str,
        params: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        """
        Attempt to reconnect using exponential backoff.
        Stops after IPC_MAX_RETRIES and marks client as exhausted.
        Must NOT hold self._lock when called (called from _send_request which holds it,
        so we release before waiting).
        """
        # This is called with the lock held from _send_request.
        # We need to release it before sleeping.
        self._lock.release()
        try:
            for attempt in range(1, IPC_MAX_RETRIES + 1):
                wait = min(IPC_RETRY_BASE_WAIT * (2 ** (attempt - 1)), IPC_RETRY_MAX_WAIT)
                logger.info(f"Reconnect attempt {attempt}/{IPC_MAX_RETRIES} in {wait:.1f}s...")
                time.sleep(wait)

                with self._lock:
                    self._close_socket()
                    self._connected = False
                    success = self._do_connect()
                    if success:
                        self._retry_count = attempt
                        # Retry the original request
                        return self._send_request(method, params, timeout)

                self._retry_count = attempt

            # Exhausted
            with self._lock:
                self._exhausted = True
                logger.error(f"IPC: Max retries ({IPC_MAX_RETRIES}) exhausted. Daemon unreachable.")
            return self._err(
                ErrorCode.DAEMON_NOT_RUNNING,
                f"Could not reconnect after {IPC_MAX_RETRIES} attempts. Check if daemon is running.",
            )
        finally:
            # Re-acquire lock so _send_request's `with self._lock` context exits cleanly
            self._lock.acquire()

    def _on_disconnect(self) -> None:
        """Called when connection drops. Must be called with lock held."""
        self._close_socket()
        self._connected = False

    def _close_socket(self) -> None:
        """Safely close the socket. Must be called with lock held."""
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    @staticmethod
    def _err(code: str, message: str, details: dict | None = None) -> dict[str, Any]:
        """Construct a structured error response dict."""
        return {
            "success": False,
            "error_code": code,
            "message": message,
            "details": details or {},
        }
