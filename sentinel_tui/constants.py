"""
constants.py — Single source of truth for ALL configuration constants in sentinel-tui.

Never hardcode values in other modules. Always import from here.
"""

import os

# ── Protocol & Versioning ──────────────────────────────────────────────────────
PROTOCOL_VERSION         = 1          # JSON-RPC protocol version (sent in every request)
APP_VERSION              = "0.1.0"
APP_TITLE                = "Project Sentinel"
APP_SUBTITLE             = "Biometric Authentication Control Interface"
EXPECTED_CONFIG_VERSION  = 1          # config.ini [Meta] config_version we expect

# ── Socket Paths ───────────────────────────────────────────────────────────────
# Production socket — created by systemd RuntimeDirectory=sentinel
PROD_SOCKET_PATH   = "/run/sentinel/sentinel.sock"
# Development socket — safe to use without root
DEV_SOCKET_PATH    = "/tmp/sentinel_test.sock"
# Resolved at runtime: env var > CLI --socket > prod default
DEFAULT_SOCKET_PATH = os.environ.get("SENTINEL_SOCKET_PATH", PROD_SOCKET_PATH)

# ── IPC Timeouts (seconds) ────────────────────────────────────────────────────
# Vary by operation type — camera/frame ops are slower than simple status checks
IPC_CONNECT_TIMEOUT  = 5     # establishing the socket connection
IPC_READ_TIMEOUT     = 15    # simple RPC calls (ping, status, get_config, etc.)
IPC_PREVIEW_TIMEOUT  = 30    # camera-heavy ops (get_devices, start_preview)
IPC_ENROLL_TIMEOUT   = 30    # enrollment frame operations
IPC_AUTH_TIMEOUT     = 10    # per-frame authentication operations
IPC_INIT_TIMEOUT     = 120   # model warmup (loading ONNX models can be slow)

# ── Retry Settings ────────────────────────────────────────────────────────────
IPC_MAX_RETRIES      = 5     # max reconnection attempts before giving up
IPC_RETRY_BASE_WAIT  = 1.0   # seconds (doubles each attempt: 1, 2, 4, 8, 16)
IPC_RETRY_MAX_WAIT   = 30.0  # cap per retry wait

# ── Refresh Intervals (seconds) ───────────────────────────────────────────────
DASHBOARD_HEALTH_INTERVAL = 3.0   # how often to refresh status cards
LOG_POLL_INTERVAL         = 1.0   # how often to poll log file / get_logs RPC
CONNECTION_PING_INTERVAL  = 5.0   # how often ConnectionStatus widget pings daemon

# ── Log Settings ──────────────────────────────────────────────────────────────
LOG_VIEWER_MAX_LINES = 500   # max lines in LogViewer buffer (FIFO)


def _resolve_log_file() -> str:
    """
    Determine the log file path to tail in the LogViewer.

    Priority:
      1. /var/log/sentinel/sentinel.log — preferred (written by daemon's sentinel_logger)
         Readable if setup.sh ran with correct group permissions.
      2. ~/.cache/sentinel/sentinel.log — user-space fallback (only if writable)
      3. /tmp/sentinel.log — last resort

    IMPORTANT: We do NOT create directories as a side effect here.
    We only verify readability (for existing files) or writability (for fallback dirs).
    """
    system_log = os.path.join(
        os.environ.get("SENTINEL_LOG_DIR", "/var/log/sentinel"),
        "sentinel.log"
    )

    # Preferred: system log (root-written, group-readable after setup.sh)
    if os.path.exists(system_log) and os.access(system_log, os.R_OK):
        return system_log

    # Fallback: user cache dir — only if it already exists OR we can create it
    user_cache_log = os.path.join(os.path.expanduser("~"), ".cache", "sentinel", "sentinel.log")
    user_cache_dir = os.path.dirname(user_cache_log)
    try:
        os.makedirs(user_cache_dir, exist_ok=True)
        # Verify we can write (needed for the logger to create the file)
        probe = os.path.join(user_cache_dir, ".probe_tui")
        with open(probe, "w") as f:
            f.write("ok")
        os.unlink(probe)
        return user_cache_log
    except OSError:
        pass

    return "/tmp/sentinel.log"


LOG_FILE = _resolve_log_file()


# ── Error Codes ───────────────────────────────────────────────────────────────
class ErrorCode:
    """
    Centralized registry of all structured error codes used across
    the TUI and daemon. Both sides share this same vocabulary.

    Usage:
        if result.get("error_code") == ErrorCode.CAMERA_NOT_FOUND:
            self.show_error("Camera disconnected")
    """
    DAEMON_NOT_RUNNING       = "DAEMON_NOT_RUNNING"
    CAMERA_NOT_FOUND         = "CAMERA_NOT_FOUND"
    CAMERA_BUSY              = "CAMERA_BUSY"
    MODELS_NOT_LOADED        = "MODELS_NOT_LOADED"
    NO_ENROLLED_USERS        = "NO_ENROLLED_USERS"
    USER_ALREADY_EXISTS      = "USER_ALREADY_EXISTS"
    USER_NOT_FOUND           = "USER_NOT_FOUND"
    SOCKET_PERMISSION_DENIED = "SOCKET_PERMISSION_DENIED"
    IPC_TIMEOUT              = "IPC_TIMEOUT"
    PROTOCOL_MISMATCH        = "PROTOCOL_MISMATCH"
    CONFIG_VERSION_MISMATCH  = "CONFIG_VERSION_MISMATCH"
    BIOMETRICS_EXPIRED       = "BIOMETRICS_EXPIRED"
    RATE_LIMITED             = "RATE_LIMITED"
    UNKNOWN                  = "UNKNOWN"

    # Human-readable descriptions for each code
    DESCRIPTIONS: dict[str, str] = {
        "DAEMON_NOT_RUNNING":       "The Sentinel daemon is not running. Start it with: sudo systemctl start sentinel-backend",
        "CAMERA_NOT_FOUND":         "Camera device not found. Check that your camera is plugged in.",
        "CAMERA_BUSY":              "Camera is in use by another process. Close other camera applications.",
        "MODELS_NOT_LOADED":        "AI models are not loaded yet. Click 'Initialize' on the dashboard.",
        "NO_ENROLLED_USERS":        "No users are enrolled. Go to Enrollment to register a face.",
        "USER_ALREADY_EXISTS":      "A user with this name is already enrolled.",
        "USER_NOT_FOUND":           "The specified user is not enrolled in the system.",
        "SOCKET_PERMISSION_DENIED": "Permission denied on socket. The daemon may need to be restarted as root.",
        "IPC_TIMEOUT":              "The daemon did not respond in time. It may be busy or overloaded.",
        "PROTOCOL_MISMATCH":        "TUI and daemon protocol versions do not match. Please update both.",
        "CONFIG_VERSION_MISMATCH":  "Configuration format is outdated. Open Settings to apply the upgrade.",
        "BIOMETRICS_EXPIRED":       "Face data is older than 45 days. Re-enrollment is required.",
        "RATE_LIMITED":             "Too many requests. Please wait a moment.",
        "UNKNOWN":                  "An unknown error occurred. Check the daemon logs for details.",
    }

    @classmethod
    def describe(cls, code: str) -> str:
        """Return a human-readable description for an error code."""
        return cls.DESCRIPTIONS.get(code, cls.DESCRIPTIONS[cls.UNKNOWN])


# ── Theme Colors ──────────────────────────────────────────────────────────────
# Used in app.tcss and referenced here for clarity
COLOR_BG           = "#0a0e1a"   # Deep navy background
COLOR_SIDEBAR      = "#111827"   # Sidebar panel
COLOR_SIDEBAR_HOVER = "#1e293b"  # Sidebar item hover
COLOR_BORDER       = "#1e3a5f"   # Panel borders
COLOR_PRIMARY      = "#00d4ff"   # Cyan accent (selected, links, highlights)
COLOR_SUCCESS      = "#00ff88"   # Green (success, running, healthy)
COLOR_ERROR        = "#ff3366"   # Red (error, failure, stopped)
COLOR_WARNING      = "#ffaa00"   # Amber (warning, degraded)
COLOR_INFO         = "#60a5fa"   # Blue (info log level)
COLOR_MUTED        = "#4b5563"   # Gray (pending, disabled, muted text)
COLOR_TEXT         = "#e2e8f0"   # Primary text
COLOR_TEXT_DIM     = "#94a3b8"   # Secondary/dim text

# ── Auth Zone Colors ──────────────────────────────────────────────────────────
ZONE_GOLDEN   = "#ffd700"   # Gold — best match, instant access
ZONE_STANDARD = COLOR_SUCCESS  # Green — standard access
ZONE_2FA      = COLOR_WARNING  # Amber — two-factor required
ZONE_FAILURE  = COLOR_ERROR    # Red — access denied
