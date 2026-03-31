#!/usr/bin/env python3
"""
sentinel_logger.py — Shared logging setup for Project Sentinel.

All modules import from here so that logging is consistent and always
writes to a discoverable file regardless of how the daemon was started.

Log file location (in priority order):
  1. $SENTINEL_LOG_DIR/sentinel.log    (user-defined override)
  2. /var/log/sentinel/sentinel.log    (standard — created by systemd LogsDirectory=sentinel)
  3. /tmp/sentinel_debug.log           (fallback for non-root testing)

Retention policy:
  - Rotates daily at midnight.
  - Keeps 30 days of history (backupCount=30).
  - Archived files are named: sentinel.log.YYYY-MM-DD
  - Log lines are written as JSON for machine-readable parsing.
"""

import os
import sys
import logging
import traceback
from logging.handlers import TimedRotatingFileHandler

# ── Determine a writable log directory ───────────────────────────────────────

def _resolve_log_dir() -> str:
    candidates = [
        os.environ.get("SENTINEL_LOG_DIR", ""),   # user override
        "/var/log/sentinel",                        # systemd managed
        "/tmp",                                     # last resort
    ]
    for path in candidates:
        if not path:
            continue
        try:
            os.makedirs(path, exist_ok=True)
            # Make log directory accessible to root only (sudo required to read)
            try:
                os.chmod(path, 0o750)
            except Exception:
                pass
            # Quick write test
            probe = os.path.join(path, ".probe")
            with open(probe, "w") as f:
                f.write("ok")
            os.unlink(probe)
            return path
        except Exception:
            continue
    return "/tmp"  # should never reach here


LOG_DIR = _resolve_log_dir()
LOG_FILE = os.path.join(LOG_DIR, "sentinel.log")

# ── Formatter ─────────────────────────────────────────────────────────────────

class JSONFormatter(logging.Formatter):
    """JSON structured log lines."""
    def format(self, record):
        import json
        from datetime import datetime
        log_obj = {
            "timestamp": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "level": record.levelname,
            "component": record.name.split('.')[-1] if '.' in record.name else record.name,
            "message": record.getMessage()
        }
        if record.exc_info:
            log_obj["traceback"] = "".join(traceback.format_exception(*record.exc_info)).rstrip()
        return json.dumps(log_obj)

class PlainFormatter(logging.Formatter):
    """Human-readable log lines, easy to grep."""
    FMT = "%(asctime)s  [%(levelname)-8s]  %(name)s — %(message)s"
    DATEFMT = "%Y-%m-%d %H:%M:%S"

    def __init__(self):
        super().__init__(fmt=self.FMT, datefmt=self.DATEFMT)

    def formatException(self, ei):
        """Include full traceback in the log."""
        return "".join(traceback.format_exception(*ei)).rstrip()


# ── Setup function ─────────────────────────────────────────────────────────────

_configured = False

def setup(name: str = "Sentinel", level: int = logging.DEBUG) -> logging.Logger:
    """
    Call once from sentinel_service.py main module.
    Returns the root-level 'Sentinel' logger.
    All child loggers (Sentinel.biometric, etc.) inherit handlers automatically.
    """
    global _configured
    if _configured:
        return logging.getLogger(name)

    root = logging.getLogger(name)
    root.setLevel(level)

    # 1. Daily rotating file handler — one file per day, 30-day FIFO retention
    try:
        fh = TimedRotatingFileHandler(
            LOG_FILE,
            when="midnight",     # rotate at 00:00 local time
            interval=1,          # every 1 day
            backupCount=30,      # keep 30 days of history, then delete oldest
            encoding="utf-8",
            utc=False,           # use local system time for rollover
        )
        # Archived files: sentinel.log.YYYY-MM-DD
        fh.suffix = "%Y-%m-%d"
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(JSONFormatter())
        root.addHandler(fh)
    except Exception as e:
        print(f"[sentinel_logger] WARNING: Cannot open log file {LOG_FILE}: {e}", file=sys.stderr)

    # 2. Console handler — captured by systemd journal
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(PlainFormatter())
    root.addHandler(ch)

    _configured = True

    # Write an obvious startup banner so we know logging is working
    root.info("=" * 60)
    root.info("  Project Sentinel Daemon — startup")
    root.info(f"  Log file: {LOG_FILE}")
    root.info(f"  Python:   {sys.executable}")
    root.info(f"  PID:      {os.getpid()}")
    root.info(f"  UID:      {os.getuid()}")
    root.info(f"  CWD:      {os.getcwd()}")
    root.info("=" * 60)

    return root


def get(module_name: str) -> logging.Logger:
    """Get a child logger. Call after setup()."""
    return logging.getLogger(f"Sentinel.{module_name}")
