#!/usr/bin/env python3
"""
sentinel_service.py - JSON-RPC Unix Socket Daemon for Project Sentinel
Provides IPC interface between Vala GTK4 UI and Python biometric processor.
Runs as a persistent service to keep models warm.
"""

# ── STEP 0: Wire up file logging BEFORE any other import so crashes are captured ──
# This is the very first thing the process does so we never lose error messages.
import os, sys

# Bootstrap: add the project source directory to sys.path so sentinel_logger
# can be found whether we are running from source OR from a pip-installed venv.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

# Also check the working directory (systemd WorkingDirectory=/usr/lib/project-sentinel)
_WORK_DIR = os.getcwd()
if _WORK_DIR not in sys.path:
    sys.path.insert(0, _WORK_DIR)

try:
    import sentinel_logger as _slog
    _log = _slog.setup("Sentinel")
    _log.info("sentinel_logger loaded OK — logging is active")
except Exception as _boot_err:
    # Absolute last resort: stderr only (still captured by journalctl)
    import logging
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )
    _log = logging.getLogger("Sentinel")
    _log.error(f"Could not load sentinel_logger: {_boot_err}")

# Global exception hook — any unhandled exception gets logged to our log file
def _excepthook(exc_type, exc_value, exc_tb):
    _log.critical("UNHANDLED EXCEPTION — daemon is about to crash",
                  exc_info=(exc_type, exc_value, exc_tb))
sys.excepthook = _excepthook

_log.info("Starting imports...")

# ── STEP 1: Standard library imports ──────────────────────────────────────────
import socket
import json
import logging
import threading
import time
import base64
import pwd
import traceback
import subprocess

_log.info("stdlib imports OK")

# ── STEP 2: Dependencies ───────────────────────────────────────────────────────
from threading import Thread, Lock, Event

# ── STEP 3: Quiet C++ library noise ───────────────────────────────────────────
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['GLOG_minloglevel'] = '3'
os.environ['LIBGL_DEBUG'] = 'quiet'
os.environ['MESA_DEBUG'] = 'silent'

import warnings
warnings.filterwarnings('ignore')

# ── STEP 4: Get module-level logger ───────────────────────────────────────────
logger = logging.getLogger("Sentinel.service")

# ── STEP 5: Silence C-level stdout during heavy imports ───────────────────────

class LowLevelSilence:
    """
    Redirects file descriptor 1 (stdout) to /dev/null to silence
    C libraries (TensorFlow, EGL, OpenCV) that bypass Python's sys.stdout.
    """
    def __enter__(self):
        sys.stdout.flush()
        self.save_fd = os.dup(1)
        self.devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(self.devnull, 1)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout.flush()
        os.dup2(self.save_fd, 1)
        os.close(self.devnull)
        os.close(self.save_fd)

# ── STEP 6: Global lazy-load placeholders ─────────────────────────────────────
cv2 = None
np = None
BiometricProcessor = None
BiometricConfig = None
LivenessValidator = None
FaceEmbeddingStore = None
SentinelAuthenticator = None
CameraStream = None

_log.info("Top-level module setup complete")

# ---- DAEMON SOCKET CONFIG ----
DEFAULT_SOCKET_PATH = os.environ.get("SENTINEL_SOCKET_PATH", "/run/sentinel/sentinel.sock")
SOCKET_BACKLOG = 10
# 0o666: world-readable so the desktop user (running the Vala UI) can connect
# to the daemon which runs as root. The socket is on a tmpfs inside /run/sentinel
# so this does not expose persistent data — only the IPC channel itself.
SOCKET_MODE = 0o666

class SentinelService:
    """JSON-RPC service wrapper for biometric processor"""
    
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.config = None
        self.processor = None
        self.validator = None
        self.store = None
        self.authenticator = None
        self.camera = None
        self.current_mode = None
        self.lock = Lock()
        self._start_time = time.time()  # Track daemon start time for uptime reporting
        
        # Enrollment state
        self.enroll_user = None
        self.enroll_poses = []
        self.enroll_current_pose = 0
        self.enroll_gallery = []

        # Daemon warmup state
        self.warmed = False
        self.warmup_error = None
        self.init_in_progress = False
        self._init_done = Event()
        self._init_done.set()
        
    def initialize(self, params):
        """Initialize the biometric processor and models (Thread-safe, Idempotent)"""
        global cv2, np, BiometricProcessor, BiometricConfig, LivenessValidator, FaceEmbeddingStore, SentinelAuthenticator, CameraStream
        
        # Fast path if already warmed
        if self.warmed:
            return {"success": True, "already": True}

        # If another thread is initializing, wait for it
        if self.init_in_progress:
            timeout = float(params.get("timeout_sec", 120)) if isinstance(params, dict) else 120.0
            self._init_done.wait(timeout=timeout)
            if self.warmed:
                return {"success": True, "already": True}
            return {"success": False, "error": self.warmup_error or "Initialization in progress"}

        self.init_in_progress = True
        self._init_done.clear()
        
        try:
            self.logger.info("Initializing Sentinel Service (Warmup)...")
            
            # Lazy import heavy libraries
            with LowLevelSilence():
                import cv2 as _cv2
                import numpy as _np
                from biometric_processor import (
                    BiometricProcessor as _BP,
                    BiometricConfig as _BC,
                    LivenessValidator as _LV,
                    FaceEmbeddingStore as _FES,
                    SentinelAuthenticator as _SA
                )
                from camera_stream import CameraStream as _CS
                
                cv2 = _cv2
                np = _np
                BiometricProcessor = _BP
                BiometricConfig = _BC
                LivenessValidator = _LV
                FaceEmbeddingStore = _FES
                SentinelAuthenticator = _SA
                CameraStream = _CS
                
                # Force re-enable stdout buffering for python logic if needed
                # (LowLevelSilence restores original FD, but libraries might have messed with buffers)
                pass

            self.config = BiometricConfig()
            self.processor = BiometricProcessor()
            self.validator = LivenessValidator()
            self.store = FaceEmbeddingStore()
            
            if not self.processor.initialize_models():
                self.warmup_error = "Failed to initialize models"
                self.warmed = False
                return {"success": False, "error": self.warmup_error}
            
            self.warmup_error = None
            self.warmed = True
            self.logger.info("Sentinel Service warmup complete.")
            return {"success": True}
        except Exception as e:
            self.warmup_error = str(e)
            self.warmed = False
            self.logger.error(f"Failed during warmup/initialization: {e}")
            self.logger.error(traceback.format_exc())
            return {"success": False, "error": self.warmup_error}
        finally:
            self.init_in_progress = False
            self._init_done.set()

    def status(self, params):
        """Lightweight status check"""
        return {
            "success": True,
            "warmed": bool(self.warmed),
            "init_in_progress": bool(self.init_in_progress),
            "error": self.warmup_error
        }

    # --- CORE METHODS ---

    def start_authentication(self, params):
        try:
            target_user = params.get('user', None)
            
            # Re-verify store loaded
            if not self.store: self.store = FaceEmbeddingStore()
            
            user_galleries, user_names = self.store.load_all_galleries()
            if not user_galleries:
                return {"success": False, "error": "No enrolled users found"}
            
            if target_user and self.store.check_expiry(target_user, max_days=45):
                 return {"success": False, "error": "BIOMETRICS_EXPIRED"}
            
            try:
                from instruction_manager import InstructionManager
                if not hasattr(self, 'instruction_manager') or not self.instruction_manager:
                    self.instruction_manager = InstructionManager()
                self.authenticator = SentinelAuthenticator(target_user=target_user, instruction_manager=self.instruction_manager)
            except Exception as e:
                self.logger.warning(f"Failed to init InstructionManager for TUI: {e}")
                self.authenticator = SentinelAuthenticator(target_user=target_user)
                
            if not self.authenticator.initialize():
                return {"success": False, "error": self.authenticator.message}
            
            width = self.config.config.getint('Camera', 'width', fallback=640)
            height = self.config.config.getint('Camera', 'height', fallback=480)
            fps = self.config.config.getint('Camera', 'fps', fallback=15)
            
            if self.camera: self.camera.stop()
            self.camera = CameraStream(src=self.config.CAMERA_INDEX, width=width, height=height, fps=fps).start()
            
            if self.camera is None or not getattr(self.camera, 'grabbed', False):
                self.camera = None
                self.current_mode = None
                return {"success": False, "error": "Camera opened but no valid frames were received"}

            self.current_mode = 'auth'
            
            return {"success": True, "users": user_names, "target_user": target_user}
        except Exception as e:
            self.logger.error(f"Start auth error: {e}")
            return {"success": False, "error": str(e)}

    def process_auth_frame(self, params):
        if self.current_mode not in ('auth', 'pam_auth') or not self.camera or not self.authenticator:
            return {"success": False, "error": "Authentication not started"}
        
        try:
            frame = self.camera.read()
            if frame is None:
                return {
                    "success": True,
                    "state": "ERROR",
                    "message": "Camera disconnected or frozen. Please check USB connection.",
                    "face_box": None,
                    "info": {},
                    "frame": ""
                }
            
            state, message, face_box, info = self.authenticator.process_frame(frame)
            
            # ──────────────────────────────────────────────────────────────────
            # PROTOTYPE-STYLE VISUAL FEEDBACK: Draw face box with state coloring
            # ──────────────────────────────────────────────────────────────────
            if face_box is not None and len(face_box) >= 4:
                try:
                    x, y, w, h = [int(float(v)) for v in face_box[:4]]
                    
                    # ──────────────────────────────────────────────────────────
                    # 1. COLOR-CODED BOX BY STATE (matching prototype)
                    # ──────────────────────────────────────────────────────────
                    # Map state to BGR color (OpenCV uses BGR not RGB)
                    state_colors = {
                        "SUCCESS": (0, 255, 0),           # Green
                        "RECOGNIZED": (0, 255, 0),       # Green (challenged)
                        "REQUIRE_2FA": (0, 165, 255),    # Orange
                        "FAILURE": (0, 0, 255),          # Red
                        "LOCKOUT": (0, 0, 255),          # Red
                        "ERROR": (0, 0, 255),            # Red
                        "WAITING": (0, 255, 255),        # Yellow
                    }
                    box_color = state_colors.get(str(state), (0, 255, 255))  # Default yellow
                    
                    # Main bounding box (thickness 2)
                    cv2.rectangle(frame, (x, y), (x + w, y + h), box_color, 2)
                    
                    # ──────────────────────────────────────────────────────────
                    # 2. CORNER ACCENT LINES (decorative, like prototype)
                    # ──────────────────────────────────────────────────────────
                    corner_len = max(15, min(w, h) // 8)  # Scale corners with box size
                    corner_thickness = 2
                    
                    # Top-left corner
                    cv2.line(frame, (x, y), (x + corner_len, y), box_color, corner_thickness)
                    cv2.line(frame, (x, y), (x, y + corner_len), box_color, corner_thickness)
                    
                    # Top-right corner
                    cv2.line(frame, (x + w, y), (x + w - corner_len, y), box_color, corner_thickness)
                    cv2.line(frame, (x + w, y), (x + w, y + corner_len), box_color, corner_thickness)
                    
                    # Bottom-left corner
                    cv2.line(frame, (x, y + h), (x + corner_len, y + h), box_color, corner_thickness)
                    cv2.line(frame, (x, y + h), (x, y + h - corner_len), box_color, corner_thickness)
                    
                    # Bottom-right corner
                    cv2.line(frame, (x + w, y + h), (x + w - corner_len, y + h), box_color, corner_thickness)
                    cv2.line(frame, (x + w, y + h), (x + w, y + h - corner_len), box_color, corner_thickness)
                    
                    # ──────────────────────────────────────────────────────────
                    # 3. CONFIDENCE PERCENTAGE BELOW BOX (like prototype)
                    # ──────────────────────────────────────────────────────────
                    if isinstance(info, dict):
                        confidence = info.get('confidence', None)
                        if confidence is not None:
                            try:
                                conf_value = float(confidence) if isinstance(confidence, (int, float)) else 0.0
                                conf_text = f"{conf_value * 100:.1f}%"
                                
                                # Text position: below box, centered
                                text_x = x + (w // 2)
                                text_y = y + h + 25
                                
                                # Get text size for background box
                                font = cv2.FONT_HERSHEY_SIMPLEX
                                font_scale = 0.6
                                font_thickness = 2
                                text_size = cv2.getTextSize(conf_text, font, font_scale, font_thickness)
                                text_width = text_size[0][0]
                                text_height = text_size[0][1]
                                
                                # Draw semi-transparent background rectangle
                                overlay = frame.copy()
                                bg_x1 = text_x - (text_width // 2) - 5
                                bg_y1 = text_y - text_height - 5
                                bg_x2 = text_x + (text_width // 2) + 5
                                bg_y2 = text_y + 5
                                cv2.rectangle(overlay, (bg_x1, bg_y1), (bg_x2, bg_y2), (0, 0, 0), -1)
                                frame = cv2.addWeighted(overlay, 0.6, frame, 0.4, 0)
                                
                                # Draw confidence text
                                cv2.putText(frame, conf_text, (text_x - (text_width // 2), text_y),
                                          font, font_scale, box_color, font_thickness)
                            except Exception as e:
                                self.logger.debug(f"Failed to draw confidence: {e}")
                    
                    # ──────────────────────────────────────────────────────────
                    # 4. STATE TEXT LABEL ABOVE BOX removed in prototype-style preview
                    # ──────────────────────────────────────────────────────────
                
                except Exception as e:
                    self.logger.debug(f"Failed to draw enhanced face box: {e}")

            # Encode frame as base64 JPEG for preview
            _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            frame_b64 = base64.b64encode(buffer).decode('utf-8')
            
            # Sanitize face_box to strict Python ints for Vala UI
            safe_face_box = None
            if face_box is not None:
                try:
                    safe_face_box = [int(float(x)) for x in face_box[:4]]
                except Exception:
                    pass
            
            # Sanitize info dict (convert any NumPy scalars to native Python types)
            safe_info = {}
            if isinstance(info, dict):
                for k, v in info.items():
                    if hasattr(v, 'item'):
                        safe_info[k] = v.item()
                    elif isinstance(v, (int, float, str, bool)):
                        safe_info[k] = v
                    else:
                        safe_info[k] = str(v)

            return {
                "success": True,
                "state": str(state) if state is not None else "UNKNOWN",
                "message": str(message) if message is not None else "",
                "face_box": safe_face_box,
                "info": safe_info,
                "frame": frame_b64 if frame_b64 is not None else ""
            }
        except Exception as e:
            import traceback
            self.logger.error(f"Frame error traceback:\n{traceback.format_exc()}")
            return {"success": False, "error": str(e)}

    def stop_authentication(self, params):
        try:
            if self.camera:
                self.camera.stop()
                self.camera = None
            self.authenticator = None
            self.current_mode = None
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _discover_gui_context(self, target_user=None):
        """Attempts to find the active GUI session on the local machine (seat0) using loginctl.
        
        Hardened to handle:
        - loginctl output format: SESSION UID USER SEAT CLASS TYPE TTY REMOTE IDLE
        - Both Wayland and X11 sessions
        - Missing/empty WaylandDisplay/Display properties
        - DBUS_SESSION_BUS_ADDRESS discovery via /run/user/<uid>/bus
        - xauth file discovery via /run/user/<uid>/gdm/Xauthority or ~/.Xauthority
        """
        import glob as _glob
        import subprocess
        context = {}
        try:
            result = subprocess.run(
                ["loginctl", "list-sessions", "--no-legend"],
                capture_output=True, text=True, timeout=5
            )
            # loginctl columns (--no-legend): SESSION UID USER SEAT CLASS TYPE TTY REMOTE IDLE
            # Older systemd may have fewer columns. seat0 is always in column 3 (0-indexed).
            best_session = None  # (session_id, uid, user, props)

            for line in result.stdout.strip().split('\n'):
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) < 4:
                    continue
                session_id = parts[0]
                uid        = parts[1]
                user       = parts[2]
                seat       = parts[3]   # 'seat0' or '-'

                if seat != "seat0":
                    continue

                # Query all useful properties in one shot
                prop_res = subprocess.run(
                    ["loginctl", "show-session", session_id,
                     "-p", "Display",
                     "-p", "WaylandDisplay",
                     "-p", "Remote",
                     "-p", "State",
                     "-p", "Type"],
                    capture_output=True, text=True, timeout=5
                )
                props = {}
                for prop_line in prop_res.stdout.strip().split('\n'):
                    if '=' in prop_line:
                        k, v = prop_line.split('=', 1)
                        props[k.strip()] = v.strip()

                if props.get("Remote", "no") == "yes":
                    continue

                state   = props.get("State", "")
                wayland = props.get("WaylandDisplay", "")
                display = props.get("Display", "")
                stype   = props.get("Type", "")

                # Accept 'active' sessions first; fall through to 'online' if nothing better
                has_display = bool(wayland or display)
                if state == "active" and (has_display or stype in ("wayland", "x11", "mir")):
                    best_session = (session_id, uid, user, props)
                    break  # active seat0 session wins immediately
                elif state in ("online", "opening") and has_display and best_session is None:
                    best_session = (session_id, uid, user, props)

            if best_session:
                session_id, uid, user, props = best_session
                wayland = props.get("WaylandDisplay", "")
                display = props.get("Display", "")
                xdg     = f"/run/user/{uid}"

                # ── If WaylandDisplay is empty, try common fallbacks ──
                if not wayland and os.path.exists(f"{xdg}/wayland-0"):
                    wayland = "wayland-0"
                if not wayland and os.path.exists(f"{xdg}/wayland-1"):
                    wayland = "wayland-1"

                # ── Xauthority: try several locations ──
                xauth = ""
                for xauth_candidate in [
                    f"{xdg}/gdm/Xauthority",
                    f"{xdg}/.Xauthority",
                    f"/home/{user}/.Xauthority",
                ]:
                    if os.path.exists(xauth_candidate):
                        xauth = xauth_candidate
                        break
                # Also try glob for xauth_* files in xdg runtime
                if not xauth:
                    matches = _glob.glob(f"{xdg}/xauth_*")
                    if matches:
                        xauth = matches[0]

                # ── DBUS: required for many Wayland compositors / GTK apps ──
                dbus_addr = ""
                dbus_socket = f"{xdg}/bus"
                if os.path.exists(dbus_socket):
                    dbus_addr = f"unix:path={dbus_socket}"

                context['xdg_runtime_dir'] = xdg
                if wayland:
                    context['wayland_display'] = wayland
                if display:
                    context['display'] = display
                if xauth:
                    context['xauthority'] = xauth
                if dbus_addr:
                    context['dbus_session_bus_address'] = dbus_addr
                context['uid'] = uid
                context['user'] = user

                self.logger.info(
                    f"PAM: Discovered session {session_id} for user '{user}' (uid={uid}) "
                    f"[Wayland={wayland or 'N/A'}, Display={display or 'N/A'}, "
                    f"XDG={xdg}, DBUS={dbus_addr or 'N/A'}]"
                )
            else:
                self.logger.warning("PAM: No active seat0 GUI session found via loginctl.")

        except Exception as e:
            self.logger.warning(f"PAM: GUI discovery failed: {e}", exc_info=True)
        return context

    # --- HEADLESS PAM AUTHENTICATION ---
    def authenticate_pam(self, params):
        """
        Headless PAM authentication: reuses the daemon's already-warmed models.
        Runs a camera+recognition loop for up to 11s, streams frames to the
        shared frame buffer so process_auth_frame can serve the preview window
        in parallel (the daemon is threaded per-client).
        """
        target_user = params.get('user')
        on_update = params.get('_on_update', None)
        self.logger.info(f"PAM: Authentication request for user '{target_user}'")
        
        # ── Ensure models are warmed (daemon init already does this at startup) ──
        if not self.warmed or not self.processor:
            self.logger.info("PAM: Daemon not yet warmed, triggering init...")
            result = self.initialize({"timeout_sec": 30})
            if not result.get('success'):
                err_msg = result.get('error', 'Daemon warmup timed out')
                self.logger.error(f"PAM: Daemon warmup failed: {err_msg}")
                return {"success": True, "result": "FAILED", "error": f"Warmup Failed: {err_msg}"}

        try:
            # ── Discover GUI/display context ──
            gui_context = params.get('gui_context', {})
            if not gui_context.get('wayland_display') and not gui_context.get('display'):
                gui_context.update(self._discover_gui_context(target_user))

            display         = gui_context.get('display')
            wayland_display = gui_context.get('wayland_display')
            xdg_runtime     = gui_context.get('xdg_runtime_dir')
            xauth           = gui_context.get('xauthority')
            can_preview     = bool(display or wayland_display)

            self.logger.info(f"PAM: GUI context → display={display} wayland={wayland_display} xdg={xdg_runtime}")

            # ── Build a SentinelAuthenticator that REUSES already-loaded models ──
            try:
                from instruction_manager import InstructionManager
                if not hasattr(self, 'instruction_manager') or not self.instruction_manager:
                    self.instruction_manager = InstructionManager()
                # Inject target user so audio can route properly into the target user's PulseAudio/PipeWire session
                self.instruction_manager.target_user = target_user
                
                if on_update:
                    def instruction_hook(text_msg):
                        if text_msg: on_update(text_msg)
                    self.instruction_manager.on_text_generated = instruction_hook
                    # Send an immediate informational update so PAM clients (sudo) get
                    # feedback right away instead of waiting for the first instruction
                    # from the recognition loop.
                    try:
                        on_update("Looking for face...")
                    except Exception:
                        self.logger.debug("PAM: initial on_update failed", exc_info=True)
                else:
                    self.instruction_manager.on_text_generated = None
                
                auth = SentinelAuthenticator(
                    target_user=target_user, headless=True,
                    instruction_manager=self.instruction_manager
                )
            except Exception as e:
                self.logger.warning(f"PAM: InstructionManager init failed (non-fatal): {e}")
                auth = SentinelAuthenticator(target_user=target_user, headless=True)

            auth.processor = self.processor

            # ── Initialize the authenticator (loads models/galleries) ──
            if not auth.initialize():
                err_reason = getattr(auth, 'message', 'Initialization failed')
                self.logger.error(f"PAM: Auth initialize failed: {err_reason}")
                return {"success": True, "result": "FAILED", "error": f"Init Failed: {err_reason}"}

            # ── Load galleries ──
            # The galleries are already loaded within auth.initialize() but we re-fetch to log
            galleries, _ = self.store.load_all_galleries()
            if not galleries:
                return {"success": True, "result": "FAILED", "error": "No enrolled users"}

            if target_user:
                if target_user in galleries:
                    # Exact match: only check this user's face
                    auth.galleries = {target_user: galleries[target_user]}
                else:
                    # PAM username (e.g. 'mehulgolecha') doesn't match gallery name (e.g. 'mehul').
                    # On a personal device this is always the owner — fall through to any-gallery auth.
                    self.logger.warning(
                        f"PAM: '{target_user}' not in gallery {list(galleries.keys())}. "
                        f"Falling back to any-enrolled-user authentication."
                    )
                    auth.galleries = galleries  # any enrolled face unlocks
            else:
                auth.galleries = galleries

            auth.session_start_time = time.time()

            # ── Launch preview subprocess (as session user for Wayland access) ──
            preview_proc = None
            if can_preview:
                try:
                    SENTINEL_ROOT  = "/usr/lib/project-sentinel"
                    preview_script = os.path.join(SENTINEL_ROOT, "sentinel_tui", "scripts", "frame_preview.py")
                    # The preview uses GStreamer (gi.repository.Gst) — a SYSTEM package.
                    # Must use system python3, NOT the venv python (3.11), because
                    # gi.repository is NOT installed inside the venv.
                    python_bin = "/usr/bin/python3"
                    if not os.path.exists(python_bin):
                        # Fallback: venv python has OpenCV fallback in frame_preview.py
                        python_bin = os.path.join(SENTINEL_ROOT, "venv", "bin", "python3")
                        self.logger.warning(f"PAM: /usr/bin/python3 not found, falling back to {python_bin}")

                    if not os.path.exists(preview_script):
                        self.logger.error(f"PAM: Preview script not found at {preview_script}")
                    elif not os.path.exists(python_bin):
                        self.logger.error(f"PAM: Python binary not found at {python_bin}")
                    else:
                        # ── Build minimal but complete environment for the preview process ──
                        # We start clean (no parent env leakage) and add only what is needed.
                        env = {
                            'PATH':              '/usr/local/bin:/usr/bin:/bin',
                            'PYTHONPATH':        SENTINEL_ROOT,
                            'PYTHONDONTWRITEBYTECODE': '1',
                            'HOME':              f"/home/{target_user}" if target_user else '/root',
                            # OpenCV / Mesa need these silenced
                            'LIBGL_DEBUG':       'quiet',
                            'MESA_DEBUG':        'silent',
                        }

                        # Display/session variables
                        if display:         env['DISPLAY']                   = display
                        if wayland_display: env['WAYLAND_DISPLAY']           = wayland_display
                        if xdg_runtime:     env['XDG_RUNTIME_DIR']           = xdg_runtime
                        if xauth:           env['XAUTHORITY']                = xauth

                        # DBUS is required by most Wayland compositors & GTK (incl. OpenCV GTK backend)
                        dbus_addr = gui_context.get('dbus_session_bus_address', '')
                        if dbus_addr:
                            env['DBUS_SESSION_BUS_ADDRESS'] = dbus_addr
                        elif xdg_runtime and os.path.exists(f"{xdg_runtime}/bus"):
                            env['DBUS_SESSION_BUS_ADDRESS'] = f"unix:path={xdg_runtime}/bus"

                        # SENTINEL_SOCKET_PATH so the preview knows where to connect
                        env['SENTINEL_SOCKET_PATH'] = DEFAULT_SOCKET_PATH

                        # ── CRITICAL: Build command with env vars baked in ──
                        #
                        # Problem: runuser -u user -- python3 script.py
                        #   Popen(env=X) only sets the environment for the 'runuser'
                        #   process itself. runuser then creates a FRESH PAM-based
                        #   environment for the child process, discarding WAYLAND_DISPLAY,
                        #   XDG_RUNTIME_DIR, DBUS_SESSION_BUS_ADDRESS, etc.
                        #
                        # Fix: use /usr/bin/env VAR=value ... inside the runuser invocation
                        #   so the env vars are set AS part of the spawned process:
                        #   runuser -u user -- /usr/bin/env WAYLAND=... python3 script.py

                        # Build KEY=VALUE pairs for /usr/bin/env
                        env_pairs = [f"{k}={v}" for k, v in env.items()]

                        inner_cmd = ["/usr/bin/env"] + env_pairs + [
                            python_bin, preview_script, "--mode", "auth",
                            "--socket", DEFAULT_SOCKET_PATH
                        ]

                        if target_user:
                            # runuser -u <user> -- /usr/bin/env KEY=VALUE ... python3 ...
                            cmd_args = ["runuser", "-u", target_user, "--"] + inner_cmd
                            popen_env = None  # don't set env= on runuser itself
                        else:
                            # No user switch needed: pass env directly to python3
                            cmd_args = [python_bin, preview_script, "--mode", "auth",
                                        "--socket", DEFAULT_SOCKET_PATH]
                            popen_env = env

                        log_path = "/var/log/sentinel/preview.log"
                        self.logger.info(f"PAM: Launching preview (user='{target_user}')")
                        self.logger.info(f"PAM: Preview cmd: {' '.join(cmd_args[:10])} ...")
                        self.logger.info(
                            f"PAM: Preview env: WAYLAND={env.get('WAYLAND_DISPLAY','')}, "
                            f"DISPLAY={env.get('DISPLAY','')}, "
                            f"XDG={env.get('XDG_RUNTIME_DIR','')}, "
                            f"DBUS={env.get('DBUS_SESSION_BUS_ADDRESS','')}"
                        )
                        try:
                            log_file = open(log_path, "a")
                            log_file.write(f"\n{'='*60}\n")
                            log_file.write(f"PAM preview: {time.ctime()} | user={target_user}\n")
                            log_file.write(f"  cmd: {' '.join(cmd_args)}\n")
                            log_file.write(f"  WAYLAND_DISPLAY: {env.get('WAYLAND_DISPLAY','')}\n")
                            log_file.write(f"  DISPLAY:         {env.get('DISPLAY','')}\n")
                            log_file.write(f"  XDG_RUNTIME_DIR: {env.get('XDG_RUNTIME_DIR','')}\n")
                            log_file.write(f"  DBUS:            {env.get('DBUS_SESSION_BUS_ADDRESS','')}\n")
                            log_file.write(f"{'='*60}\n")
                            log_file.flush()
                            preview_proc = subprocess.Popen(
                                cmd_args,
                                env=popen_env,     # None for runuser case (runuser sets its own env)
                                stdout=log_file,
                                stderr=log_file,
                                close_fds=True,
                            )
                        except OSError as le:
                            self.logger.warning(f"PAM: Preview log open failed ({le}), DEVNULL fallback")
                            preview_proc = subprocess.Popen(
                                cmd_args, env=popen_env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                close_fds=True,
                            )
                        self.logger.info(f"PAM: Preview launched, pid={preview_proc.pid}")
                except Exception as pe:
                    self.logger.error(f"PAM: Preview launch failed: {pe}", exc_info=True)

            # ── Audio / Text Guidance Fallback Logic ──
            # If the preview window failed to open, or exited immediately, we rely on Audio.
            preview_running = False
            if preview_proc:
                time.sleep(0.3)  # Give GStreamer a fraction of a second to fail if Wayland socket is blocked
                if preview_proc.poll() is None:
                    preview_running = True
                else:
                    self.logger.warning(f"PAM: Preview window failed to open (exited with code {preview_proc.returncode})")

            if not preview_running:
                self.logger.info("PAM: No visual preview available. Enabling Audio/Text guidance fallback.")
                if hasattr(self, 'instruction_manager') and self.instruction_manager:
                    self.instruction_manager.update_config(preview_enabled=False, audio_enabled=True, text_enabled=True)
                    # For sudo, they can't see the UI, so play the 'Looking for face' audio
                    from instruction_manager import InstructionType
                    self.instruction_manager.send_instruction(InstructionType.LOOK_AT_CAMERA)
            else:
                self.logger.info("PAM: Visual preview is running. Enabling Text+Audio guidance on preview window.")
                if hasattr(self, 'instruction_manager') and self.instruction_manager:
                    # CRITICAL: Text must be enabled on preview window so we can overlay guidance like "Turn left", "Blink once"
                    # Send initial LOOK_AT_CAMERA prompt to preview
                    self.instruction_manager.update_config(preview_enabled=True, audio_enabled=True, text_enabled=True)
                    from instruction_manager import InstructionType
                    self.instruction_manager.send_instruction(InstructionType.LOOK_AT_CAMERA)

            # ── Open camera and run recognition loop ──
            width   = self.config.config.getint('Camera', 'width',  fallback=640)
            height  = self.config.config.getint('Camera', 'height', fallback=480)

            cam = CameraStream(src=self.config.CAMERA_INDEX, width=width, height=height, fps=15).start()
            time.sleep(0.5)  # allow camera warmup

            # Share camera + authenticator state with process_auth_frame for preview
            with self.lock:
                self.camera       = cam
                self.authenticator = auth
                self.current_mode = 'pam_auth'

            start_time = time.time()
            timeout    = 30.0  # Allow full auth including 20s challenge timeout
            status     = "FAILED"
            frame_display_callback = params.get('_on_frame_ready', None)

            try:
                while time.time() - start_time < timeout:
                    frame = cam.read()
                    if frame is None:
                        time.sleep(0.05)
                        continue

                    state, msg, _, info = auth.process_frame(frame)
                    dist = info.get('dist', 1.0) if info else 1.0
                    dist_text = f"{dist:.3f}" if isinstance(dist, (int, float)) else "N/A"

                    self.logger.debug(f"PAM frame: state={state} msg={msg} dist={dist_text}")
                    
                    # OPTIMIZATION: Send frame to callback for real-time display (Priority 3)
                    if frame_display_callback:
                        try:
                            frame_display_callback(frame)
                        except Exception as e:
                            self.logger.debug(f"PAM: Frame callback error (non-fatal): {e}")

                    if state in ("SUCCESS", "REQUIRE_2FA", "STATE_2FA"):
                        self.logger.info(f"PAM: Face matched for '{target_user}' (dist={dist:.3f})")
                        status = "SUCCESS"
                        break

                    time.sleep(0.03)
            finally:
                # Always clean up camera and shared state
                with self.lock:
                    self.camera       = None
                    self.authenticator = None
                    self.current_mode = None
                cam.stop()

            if preview_proc:
                self.logger.info("PAM: Terminating preview...")
                preview_proc.terminate()
                try:   preview_proc.wait(timeout=2.0)
                except: preview_proc.kill()

            self.logger.info(f"PAM: Result = {status}")
            if status == "FAILED":
                return {"success": True, "result": "FAILED", "error": "Face detection timed out (no face seen)"}
            
            # OPTIMIZATION: Call intrusion review callback if authentication succeeded (Priority 4)
            if status == "SUCCESS" and params.get('_on_intrusions_available'):
                try:
                    import glob
                    if not self.config: 
                        self.config = BiometricConfig()
                    blacklist_dir = self.config.BLACKLIST_DIR
                    intrusion_files = sorted(glob.glob(os.path.join(blacklist_dir, "intrusion_*.jpg")))
                    if intrusion_files:
                        self.logger.info(f"PAM: Found {len(intrusion_files)} intrusions to review")
                        params['_on_intrusions_available'](len(intrusion_files))
                except Exception as e:
                    self.logger.debug(f"PAM: Intrusion review callback failed (non-fatal): {e}")
            
            return {"success": True, "result": status}

        except Exception as e:
            self.logger.error(f"PAM: Authentication loop failed: {e}", exc_info=True)
            return {"success": True, "result": "FAILED", "error": f"Internal Error: {str(e)}"}


    # --- ENROLLMENT METHODS ---
    def start_enrollment(self, params):
        try:
            if self.processor is None: self.initialize({})
            
            user_name = params.get('user_name', '').lower().strip()
            
            if not user_name: return {"success": False, "error": "User name required"}
            
            user_galleries, user_names = self.store.load_all_galleries()
            if user_name in user_names:
                return {"success": False, "error": f"User '{user_name}' already enrolled"}
            
            # Simplified Pose Logic
            base_poses = [
                {"name": "Center", "instruction": "Look directly at the camera"},
                {"name": "Left", "instruction": "Turn head LEFT"},
                {"name": "Right", "instruction": "Turn head RIGHT"},
                {"name": "Up", "instruction": "Tilt head UP"},
                {"name": "Down", "instruction": "Tilt head DOWN"},
            ]
            
            poses = base_poses
            
            self.enroll_user = user_name
            self.enroll_poses = poses
            self.enroll_current_pose = 0
            self.enroll_gallery = []
            
            width = self.config.config.getint('Camera', 'width', fallback=640)
            height = self.config.config.getint('Camera', 'height', fallback=480)
            
            self.camera = CameraStream(src=self.config.CAMERA_INDEX, width=width, height=height, fps=15).start()
            
            if self.camera is None or not getattr(self.camera, 'grabbed', False):
                self.camera = None
                self.current_mode = None
                return {"success": False, "error": "Camera opened but no valid frames were received"}

            self.current_mode = 'enroll'
            
            return {
                "success": True, "user_name": user_name,
                "total_poses": len(poses), "current_pose": 0, "pose_info": poses[0]
            }
        except Exception as e:
            self.logger.error(f"Enroll start error: {e}")
            return {"success": False, "error": str(e)}

    def process_enroll_frame(self, params):
        if self.current_mode != 'enroll' or not self.camera:
            return {"success": False, "error": "Enrollment not started"}
        try:
            frame = self.camera.read()
            if frame is None:
                return {
                    "success": True,
                    "completed": False,
                    "current_pose": 0,
                    "total_poses": 1,
                    "pose_info": {"instruction": "Camera Disconnected/Frozen. Restart Daemon."},
                    "status": "CAMERA_FROZEN",
                    "face_box": None,
                    "frame": ""
                }
            
            if self.enroll_current_pose >= len(self.enroll_poses):
                return {"success": True, "completed": True, "message": "Enrollment complete!"}
            
            pose = self.enroll_poses[self.enroll_current_pose]
            processed_frame, faces = self.processor.detect_faces(frame)
            
            status = "no_face"
            face_box = None
            if len(faces) == 1:
                face_box = faces[0][0:4].astype(int).tolist()
                is_valid, q_stat = self.processor.validate_face_quality(faces[0])
                status = "ready" if is_valid else q_stat
            elif len(faces) > 1:
                status = "multiple_faces"
                
            # ──────────────────────────────────────────────────────────────────
            # ENHANCED ENROLLMENT FACE BOX WITH VISUAL FEEDBACK
            # ──────────────────────────────────────────────────────────────────
            if face_box is not None and len(face_box) >= 4:
                try:
                    x, y, w, h = [int(v) for v in face_box[:4]]
                    
                    # Enrollment color: Gold/Yellow for quality status
                    enroll_colors = {
                        "ready": (0, 215, 255),        # Gold (BGR)
                        "no_face": (0, 165, 255),      # Orange (no face)
                        "multiple_faces": (0, 0, 255), # Red (multiple)
                        "low_brightness": (0, 255, 255),  # Yellow (lighting issue)
                        "face_too_small": (0, 165, 255),  # Orange (distance)
                        "face_too_large": (0, 165, 255),  # Orange (distance)
                        "face_too_close": (0, 165, 255),  # Orange (distance)
                        "low_sharpness": (0, 255, 255),   # Yellow (quality)
                    }
                    box_color = enroll_colors.get(str(status), (0, 215, 255))  # Default gold
                    
                    # Main bounding box
                    cv2.rectangle(frame, (x, y), (x + w, y + h), box_color, 2)
                    
                    # Corner accent lines (matching auth style)
                    corner_len = max(15, min(w, h) // 8)
                    corner_thickness = 2
                    
                    # Top-left corner
                    cv2.line(frame, (x, y), (x + corner_len, y), box_color, corner_thickness)
                    cv2.line(frame, (x, y), (x, y + corner_len), box_color, corner_thickness)
                    
                    # Top-right corner
                    cv2.line(frame, (x + w, y), (x + w - corner_len, y), box_color, corner_thickness)
                    cv2.line(frame, (x + w, y), (x + w, y + corner_len), box_color, corner_thickness)
                    
                    # Bottom-left corner
                    cv2.line(frame, (x, y + h), (x + corner_len, y + h), box_color, corner_thickness)
                    cv2.line(frame, (x, y + h), (x, y + h - corner_len), box_color, corner_thickness)
                    
                    # Bottom-right corner
                    cv2.line(frame, (x + w, y + h), (x + w - corner_len, y + h), box_color, corner_thickness)
                    cv2.line(frame, (x + w, y + h), (x + w, y + h - corner_len), box_color, corner_thickness)
                    
                    # Status label above box
                    status_text = "READY" if str(status) == "ready" else str(status).upper()
                    font = cv2.FONT_HERSHEY_SIMPLEX
                    font_scale = 0.5
                    font_thickness = 1
                    text_size = cv2.getTextSize(status_text, font, font_scale, font_thickness)
                    text_width = text_size[0][0]
                    text_height = text_size[0][1]
                    
                    # Background for status label
                    overlay = frame.copy()
                    bg_x1 = x
                    bg_y1 = max(0, y - text_height - 8)
                    bg_x2 = x + text_width + 4
                    bg_y2 = y
                    cv2.rectangle(overlay, (bg_x1, bg_y1), (bg_x2, bg_y2), (0, 0, 0), -1)
                    frame = cv2.addWeighted(overlay, 0.7, frame, 0.3, 0)
                    
                    cv2.putText(frame, status_text, (x + 2, y - 4),
                              font, font_scale, box_color, font_thickness)
                              
                except Exception as e:
                    self.logger.debug(f"Failed to draw enrollment face box: {e}")

            _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            frame_b64 = base64.b64encode(buffer).decode('utf-8')
            
            return {
                "success": True, "completed": False,
                "current_pose": self.enroll_current_pose,
                "total_poses": len(self.enroll_poses),
                "pose_info": pose, "status": str(status) if status is not None else "ready",
                "face_box": face_box, "frame": frame_b64
            }
        except Exception as e:
             return {"success": False, "error": str(e)}

    def capture_enroll_pose(self, params):
        if self.current_mode != 'enroll': return {"success": False, "error": "Not enrolling"}
        try:
            frame = self.camera.read()
            if frame is None: return {"success": False, "error": "No frame"}
            
            processed_frame, faces = self.processor.detect_faces(frame)
            if len(faces) != 1: return {"success": False, "error": "Face detection failed"}
            
            face_box = faces[0][0:4].astype(int)
            x, y, w, h = face_box
            face_roi = frame[y:y+h, x:x+w]
            
            recognizer_input_shape = self.processor.face_recognizer.get_inputs()[0].shape[2:]
            recognizer_input_name = self.processor.face_recognizer.get_inputs()[0].name
            
            face_resized = cv2.resize(face_roi, recognizer_input_shape)
            face_rgb = cv2.cvtColor(face_resized, cv2.COLOR_BGR2RGB)
            face_transposed = np.transpose(face_rgb, (2, 0, 1))
            face_input = np.expand_dims(face_transposed, axis=0).astype('float32')
            
            embedding = self.processor.face_recognizer.run(None, {recognizer_input_name: face_input})[0]
            
            self.enroll_gallery.append(embedding)
            self.enroll_current_pose += 1
            
            if self.enroll_current_pose >= len(self.enroll_poses):
                # Save
                gallery_array = np.vstack(self.enroll_gallery)
                output_path = os.path.join(self.config.MODEL_DIR, f"gallery_{self.enroll_user}.npy")
                np.save(output_path, gallery_array)
                self.logger.info(f"Enrollment saved for {self.enroll_user}")
                
                return {"success": True, "completed": True, "message": "Enrollment Saved!"}
                
            return {
                "success": True, "completed": False, 
                "current_pose": self.enroll_current_pose,
                "pose_info": self.enroll_poses[self.enroll_current_pose]
            }
        except Exception as e:
            self.logger.error(f"Capture error: {e}")
            return {"success": False, "error": str(e)}

    def stop_enrollment(self, params):
        try:
            if self.camera: 
                self.camera.stop()
                self.camera = None
            self.current_mode = None
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # --- CONFIG & UTILS ---
    def get_config(self, params):
        if not self.config: self.config = BiometricConfig()
        cfg = self.config.config
        return {
            "success": True,
            "config": {
                'config_version': cfg.getint('Meta', 'config_version', fallback=1),
                'camera_device_id': cfg.getint('Camera', 'device_id', fallback=0),
                'camera_width': cfg.getint('Camera', 'width', fallback=640),
                'camera_height': cfg.getint('Camera', 'height', fallback=480),
                'camera_fps': cfg.getint('Camera', 'fps', fallback=15),
                'min_face_size': cfg.getint('FaceDetection', 'min_face_size', fallback=100),
                'spoof_threshold': cfg.getfloat('Liveness', 'spoof_threshold', fallback=0.92),
                'challenge_timeout': cfg.getfloat('Liveness', 'challenge_timeout', fallback=20.0),
            }
        }
    
    def update_config(self, params):
        try:
            updates = params.get('config', {}) if isinstance(params, dict) else {}
            if not updates:
                return {"success": False, "error": "No config updates provided"}
            
            import configparser
            # Use the actual config path that was loaded, or default to system location
            ini_path = None
            if hasattr(self.config, 'config_path') and self.config.config_path:
                ini_path = self.config.config_path
            else:
                # Fallback: prefer system config, then local
                if os.path.exists('/etc/project-sentinel/config.ini'):
                    ini_path = '/etc/project-sentinel/config.ini'
                else:
                    ini_path = os.path.join(os.getcwd(), 'config.ini')
            
            cfg_edit = configparser.ConfigParser()
            
            if os.path.exists(ini_path):
                cfg_edit.read(ini_path)
            
            # ME-4: Capture old values for audit trail
            changes = []
            
            # Meta Section
            if 'Meta' not in cfg_edit: cfg_edit['Meta'] = {}
            if 'config_version' in updates:
                old_value = cfg_edit.get('Meta', 'config_version', fallback='undefined')
                new_value = str(updates['config_version'])
                if old_value != new_value:
                    changes.append(f"Meta.config_version: {old_value} → {new_value}")
                cfg_edit['Meta']['config_version'] = new_value
            else: cfg_edit['Meta']['config_version'] = "1"
            
            # Camera Section
            if 'Camera' not in cfg_edit: cfg_edit['Camera'] = {}
            if 'camera_width' in updates:
                old_value = cfg_edit.get('Camera', 'width', fallback='undefined')
                new_value = str(updates['camera_width'])
                if old_value != new_value:
                    changes.append(f"Camera.width: {old_value} → {new_value}")
                cfg_edit['Camera']['width'] = new_value
            
            if 'camera_height' in updates:
                old_value = cfg_edit.get('Camera', 'height', fallback='undefined')
                new_value = str(updates['camera_height'])
                if old_value != new_value:
                    changes.append(f"Camera.height: {old_value} → {new_value}")
                cfg_edit['Camera']['height'] = new_value
            
            if 'camera_fps' in updates:
                old_value = cfg_edit.get('Camera', 'fps', fallback='undefined')
                new_value = str(updates['camera_fps'])
                if old_value != new_value:
                    changes.append(f"Camera.fps: {old_value} → {new_value}")
                cfg_edit['Camera']['fps'] = new_value
            
            # Since user manually edited, turn OFF auto_detect to lock in their choice
            cfg_edit['Camera']['auto_detect'] = 'false'
            
            # Liveness / FaceDetection Section
            if 'Liveness' not in cfg_edit: cfg_edit['Liveness'] = {}
            if 'challenge_timeout' in updates:
                old_value = cfg_edit.get('Liveness', 'challenge_timeout', fallback='undefined')
                new_value = str(updates['challenge_timeout'])
                if old_value != new_value:
                    changes.append(f"Liveness.challenge_timeout: {old_value} → {new_value}")
                cfg_edit['Liveness']['challenge_timeout'] = new_value
            
            if 'spoof_threshold' in updates:
                old_value = cfg_edit.get('Liveness', 'spoof_threshold', fallback='undefined')
                new_value = str(updates['spoof_threshold'])
                if old_value != new_value:
                    changes.append(f"Liveness.spoof_threshold: {old_value} → {new_value}")
                cfg_edit['Liveness']['spoof_threshold'] = new_value
            
            if 'FaceDetection' not in cfg_edit: cfg_edit['FaceDetection'] = {}
            if 'min_face_size' in updates:
                old_value = cfg_edit.get('FaceDetection', 'min_face_size', fallback='undefined')
                new_value = str(updates['min_face_size'])
                if old_value != new_value:
                    changes.append(f"FaceDetection.min_face_size: {old_value} → {new_value}")
                cfg_edit['FaceDetection']['min_face_size'] = new_value
            
            # Save to disk with proper permissions
            os.makedirs(os.path.dirname(ini_path), exist_ok=True)
            with open(ini_path, 'w') as configfile:
                cfg_edit.write(configfile)
                
            os.chmod(ini_path, 0o600)
            
            # Reload in memory with the explicit path to ensure we read what we just wrote
            self.config = BiometricConfig(ini_path)
            
            # ME-4: Log all config changes to audit trail
            if changes:
                change_str = " | ".join(changes)
                self.logger.info(f"CONFIG_CHANGED: {change_str} | Path={ini_path} | UID={os.getuid()}")
            else:
                self.logger.debug(f"CONFIG_UPDATE_ATTEMPTED: No actual changes (update normalized config)")
            
            return {"success": True}
        except Exception as e:
            self.logger.error(f"Error updating config: {e}")
            return {"success": False, "error": str(e)}

    def reset_config(self, params):
        """Restores config to default and re-enables auto_detect"""
        try:
            import configparser
            ini_path = self.config.config_path if hasattr(self.config, 'config_path') else 'config.ini'
            
            # Default empty config
            cfg_edit = configparser.ConfigParser()
            
            # We don't write ALL defaults, only the ones we manage. 
            # The defaults dict inside BiometricConfig will fill in the blanks.
            cfg_edit['Camera'] = {'auto_detect': 'true', 'device_id': '0'}
            
            with open(ini_path, 'w') as configfile:
                cfg_edit.write(configfile)
                
            os.chmod(ini_path, 0o600)
            
            self.config = BiometricConfig()
            self.logger.info("Configuration reset to defaults.")
            return {"success": True}
        except Exception as e:
            self.logger.error(f"Error resetting config: {e}")
            return {"success": False, "error": str(e)}

    def get_enrolled_users(self, params):
        if not self.store: self.store = FaceEmbeddingStore()
        _, names = self.store.load_all_galleries()
        return {"success": True, "users": names}
        
    def get_intrusions(self, params):
        import glob
        if not self.config: self.config = BiometricConfig()
        blacklist_dir = self.config.BLACKLIST_DIR
        images = sorted(glob.glob(os.path.join(blacklist_dir, "intrusion_*.jpg")))
        return {"success": True, "files": images}

    def delete_intrusion(self, params):
        filename = params.get('filename')
        from biometric_processor import BlacklistManager
        bm = BlacklistManager()
        bm.delete_intrusion_record(filename)
        return {"success": True}
        
    def confirm_intrusion(self, params):
        filename = params.get('filename')
        from biometric_processor import BlacklistManager
        bm = BlacklistManager()
        bm.confirm_intrusion(filename)
        return {"success": True}

    def authenticate_startup_password(self, params):
        """Standard PAM authentication for security gate"""
        password = params.get('password', '')
        if not password: return {"success": False}
        
        try:
            import pam
            p = pam.pam()
            
            # Since daemon runs as root, we look for common system users 
            # Or just authenticate against 'root' if it's a secure device.
            # Better: use the user who is likely the owner (usually UID 1000)
            import pwd
            try:
                # Find the first human user
                user_name = pwd.getpwuid(1000).pw_name
            except:
                import os
                user_name = os.environ.get('USER', 'root')

            if p.authenticate(user_name, password):
                self.logger.info(f"PAM authentication successful for user: {user_name}")
                return {"success": True}
            else:
                self.logger.warning(f"PAM authentication failed for user: {user_name}")
                return {"success": False}
        except Exception as e:
            self.logger.error(f"PAM error: {e}")
            # Fallback to simple password if PAM fails (e.g. missing lib)
            if password in ["admin", "1234"]:
                return {"success": True}
            return {"success": False, "error": str(e)}

    def ping(self, params):
        return {"success": True, "status": "alive"}

    def health(self, params):
        """Unified system health check."""
        try:
            status_summary = "healthy"
            models_state = "loaded" if self.warmed else ("loading" if self.init_in_progress else "not_loaded")

            # Camera state: 'idle' when no session (normal), 'ok' when session is active
            # and camera is open, 'error' when session is active but camera failed.
            if self.current_mode is not None:
                camera_state = "ok" if (self.camera is not None and getattr(self.camera, 'running', False)) else "error"
                if camera_state == "error":
                    status_summary = "degraded"
            else:
                camera_state = "idle"

            cfg = self.config.config if self.config else None
            cfg_ver = cfg.getint("Meta", "config_version", fallback=1) if cfg else 1

            uptime = int(time.time() - self._start_time)

            return {
                "success": True,
                "status": status_summary,
                "models": models_state,
                "camera": camera_state,
                "enrolled_users": len(self.get_enrolled_users({}).get("users", [])),
                "uptime_seconds": uptime,
                "config_version": cfg_ver
            }
        except Exception as e:
            return {"success": False, "error_code": "UNKNOWN", "message": str(e)}

    def get_devices(self, params):
        """Discover available video devices via v4l2 or simple globbing."""
        import glob
        import subprocess
        devices = []
        try:
            dev_paths = glob.glob("/dev/video*")
            for p in sorted(dev_paths):
                idx_str = p.replace("/dev/video", "")
                if not idx_str.isdigit(): continue
                idx = int(idx_str)
                name = "Unknown Camera"
                caps = "N/A"
                # Use v4l2-ctl for name extraction if available
                try:
                    res = subprocess.run(["v4l2-ctl", "-d", p, "--all"], capture_output=True, text=True, timeout=1.0)
                    for line in res.stdout.splitlines():
                        if "Card type" in line:
                            name = line.split(":", 1)[1].strip()
                            break
                    caps = "V4L2 support"
                except Exception:
                    pass
                devices.append({"index": idx, "name": name, "caps": caps})
                
            active = -1
            if self.config:
                active = self.config.config.getint("Camera", "device_id", fallback=0)
            return {"success": True, "devices": devices, "active_device": active}
        except Exception as e:
            return {"success": False, "error_code": "UNKNOWN", "message": str(e)}


# --- RPC DISPATCHER ---
def _build_methods(service: SentinelService):
    return {
        "status": service.status,
        "initialize": service.initialize,
        "start_authentication": service.start_authentication,
        "process_auth_frame": service.process_auth_frame,
        "stop_authentication": service.stop_authentication,
        "authenticate_pam": service.authenticate_pam,
        "authenticate_startup_password": service.authenticate_startup_password,
        "start_enrollment": service.start_enrollment,
        "process_enroll_frame": service.process_enroll_frame,
        "capture_enroll_pose": service.capture_enroll_pose,
        "stop_enrollment": service.stop_enrollment,
        "get_enrolled_users": service.get_enrolled_users,
        "get_config": service.get_config,
        "update_config": service.update_config,
        "reset_config": service.reset_config,
        "get_intrusions": service.get_intrusions,
        "delete_intrusion": service.delete_intrusion,
        "confirm_intrusion": service.confirm_intrusion,
        "ping": service.ping,
        "health": service.health,
        "get_devices": service.get_devices,
    }

def _rpc_error(request_id, code, message):
    return {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": request_id}

def _rpc_result(request_id, result):
    return {"jsonrpc": "2.0", "result": result, "id": request_id}

# ===== SECURITY: RPC Authorization (CR-3: Per-Method Privilege Checks) =====
def _check_rpc_permission(method_name, caller_uid):
    """
    CR-3: Checks if caller has permission for RPC method.
    Returns (allowed: bool, reason: str)
    
    This prevents unprivileged users from calling sensitive operations
    like enrollment, config changes, or intrusion record deletion.
    """
    # Public methods - anyone can call (safe operations)
    PUBLIC_METHODS = {
        'status',              # Query daemon status
        'authenticate_pam',    # Attempt biometric auth
        'process_auth_frame',  # Feed camera frame for auth
    }
    
    # Admin-only methods (require root)
    ADMIN_METHODS = {
        'initialize',          # Load models (security-critical)
        'start_enrollment',    # Begin enrollment (security-critical)
        'process_enroll_frame',# Capture enrollment frame (sensitive)
        'capture_enroll_pose', # Save enrollment pose (sensitive)
        'delete_intrusion',    # Remove intrusion record (sensitive)
        'confirm_intrusion',   # Add to blacklist (security-critical)
        'update_config',       # Modify settings (security-critical)
        'reset_system',        # Reset daemon (security-critical)
    }
    
    # Read-only methods (anyone can call, low security impact)
    READONLY_METHODS = {
        'get_config',         # Read settings
        'get_enrolled_users', # List users
        'get_intrusions',     # View intrusions
    }
    
    # CR-3: Check method against permission tiers
    if method_name in PUBLIC_METHODS:
        return (True, "Public method — no privilege required")
    
    if method_name in READONLY_METHODS:
        return (True, "Read-only method — no privilege required")
    
    if method_name in ADMIN_METHODS:
        if caller_uid == 0:
            return (True, "Running as root")
        else:
            return (False, f"Method '{method_name}' requires root privileges (uid={caller_uid})")
    
    # Unknown method (shouldn't happen if RPC dispatch catches it first)
    return (False, f"Unknown method: {method_name}")

def _get_socket_peer_uid(conn):
    """
    Attempts to get the UID of the peer process connected to this socket.
    Returns UID or None if unable to determine.
    
    Note: SO_PEERCRED is Linux-specific. On other OSes, this returns None.
    """
    try:
        import struct
        # SO_PEERCRED returns (pid, uid, gid) on Linux
        peercred = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i'))
        _, uid, _ = struct.unpack('3i', peercred)
        return uid
    except (OSError, ImportError, AttributeError):
        # Not Linux or SO_PEERCRED not available
        logger.debug("Unable to determine peer UID (may not be Linux or Unix socket)")
        return None

def _handle_rpc_line(service: SentinelService, methods: dict, line: str, conn: socket.socket=None, write_lock=None):
    try:
        request = json.loads(line)
        method = request.get("method")
        params = request.get("params", {}) or {}
        request_id = request.get("id", None)

        if request_id is None: return None # Notification

        if method not in methods:
            return _rpc_error(request_id, -32601, f"Method '{method}' not found")

        # CR-3: Check authorization before executing method
        caller_uid = _get_socket_peer_uid(conn) if conn else 0
        allowed, reason = _check_rpc_permission(method, caller_uid)
        
        if not allowed:
            logger.warning(f"RPC authorization denied: method='{method}' uid={caller_uid} reason='{reason}'")
            return _rpc_error(request_id, -32003, f"Permission denied: {reason}")

        func = methods[method]
        
        # authenticate_pam runs its OWN internal locking and holds the camera
        # loop for up to 30 seconds. It MUST NOT hold service.lock here, or
        # process_auth_frame (called by the preview subprocess) will deadlock.
        # status and authenticate_pam are both safe to call without the outer lock.
        if method in ("status", "authenticate_pam"):
             if method == "authenticate_pam" and conn and write_lock:
                 def pam_on_update(text):
                     payload = {"method": "pam_info", "params": {"text": text}}
                     try:
                         with write_lock:
                             conn.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode('utf-8'))
                     except: pass
                 params["_on_update"] = pam_on_update
             result = func(params)
        else:
             with service.lock:
                 result = func(params)
                 
        return _rpc_result(request_id, result)
        
    except json.JSONDecodeError:
        return _rpc_error(None, -32700, "Parse error")
    except Exception as e:
        logger.exception("RPC method failed: %s", e)
        return _rpc_error(request_id, -32603, str(e))

def _dispatch_request(conn: socket.socket, service: SentinelService, methods: dict, line: str, write_lock: threading.Lock):
    try:
        resp = _handle_rpc_line(service, methods, line, conn, write_lock)
        if resp:
            try:
                out = (json.dumps(resp, ensure_ascii=False) + "\n").encode('utf-8')
                with write_lock:
                    conn.sendall(out)
            except (OSError, BrokenPipeError):
                # Socket already closed, ignore silently — client has disconnected
                pass
    except Exception as e:
        logger.exception("Error processing RPC request line: %s", e)

def _handle_client(conn: socket.socket, service: SentinelService, methods: dict):
    try:
        conn.settimeout(300) # 5m timeout
        buffer = ""
        write_lock = Lock()
        request_times = []
        while True:
            chunk = conn.recv(4096)
            if not chunk: break
            
            buffer += chunk.decode('utf-8', errors='replace')
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line: continue
                
                # Rate Limiting
                now = time.time()
                request_times = [t for t in request_times if now - t < 1.0]
                if len(request_times) >= 30:
                    logger.warning("Rate limit exceeded on socket connection")
                    continue
                request_times.append(now)
                
                # Process concurrently
                Thread(target=_dispatch_request, args=(conn, service, methods, line, write_lock), daemon=True).start()
    except socket.timeout:
        pass
    except Exception as e:
        logger.exception("Client handler error: %s", e)
    finally:
        try:
             # Stop any active authentication/enrollment when client disconnects
             if service.current_mode:
                 logger.info(f"Client disconnected, stopping active {service.current_mode}...")
                 with service.lock:
                     if service.camera: service.camera.stop(); service.camera = None
                     service.current_mode = None
                     service.authenticator = None
             conn.close()
        except: pass

def _create_server_socket(socket_path):
    sock_dir = os.path.dirname(socket_path)
    os.makedirs(sock_dir, exist_ok=True, mode=0o755)
    # Ensure directory has correct permissions for socket
    try:
        os.chmod(sock_dir, 0o755)
    except Exception as e:
        logger.warning(f"Could not chmod socket directory: {e}")
    
    try: os.unlink(socket_path)
    except FileNotFoundError: pass
    
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path)
    # Important: Permissions
    try:
        os.chmod(socket_path, SOCKET_MODE) 
        # Attempt to chown to current user if running as root for testing??
        # Ideally setup.sh handles this by running as correct user or permissions
    except Exception as e:
        logger.warning(f"Could not chmod socket: {e}")
        
    server.listen(SOCKET_BACKLOG)
    return server

def main():
    service = SentinelService()
    methods = _build_methods(service)
    
    socket_path = DEFAULT_SOCKET_PATH
    logger.info(f"Sentinel daemon starting. Socket={socket_path}")
    
    try:
        server = _create_server_socket(socket_path)
    except Exception as e:
        logger.error(f"Failed to create socket: {e}")
        sys.exit(1)
        
    # Async warmup
    def _warmup():
        try:
            logger.info("Warmup thread starting...")
            # We use a long timeout for internal init
            result = service.initialize({"timeout_sec": 300})
            if result.get('success'):
                logger.info("Warmup finished successfully. Models loaded and ready.")
            else:
                logger.error(f"Warmup FAILED: {result.get('error', 'unknown error')}")
                logger.error("PAM authentication will NOT work until this is resolved.")
                logger.error("Run: sudo /usr/lib/project-sentinel/venv/bin/pip install opencv-python numpy onnxruntime scipy mediapipe")
        except Exception as e:
            logger.error(f"Warmup exception (FATAL — daemon cannot authenticate): {e}", exc_info=True)

            
    Thread(target=_warmup, daemon=True).start()
    
    logger.info("Sentinel daemon listening...")
    
    try:
        while True:
            conn, _ = server.accept()
            # Handle client in a thread
            Thread(target=_handle_client, args=(conn, service, methods), daemon=True).start()
    except KeyboardInterrupt:
        logger.info("Stopping...")
    finally:
        server.close()
        try: os.unlink(socket_path)
        except: pass

if __name__ == "__main__":
    main()
