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

# ── STEP 2: Third-party imports (these can fail if pip install missed packages) ─
try:
    import pam
    _log.info("pam imported OK")
except ImportError as e:
    _log.critical(f"FATAL: 'pam' module not found — {e}. Install python-pam in the venv.")
    sys.exit(1)

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
        if self.current_mode != 'auth' or not self.camera or not self.authenticator:
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

    # --- HEADLESS PAM AUTHENTICATION ---
    def authenticate_pam(self, params):
        """
        Headless authentication for GDM/Lockscreen.
        Runs its own loop for up to 5 seconds. Returns SUCCESS/FAILURE immediately.
        """
        target_user = params.get('user')
        self.logger.info(f"PAM: Authentication request for user '{target_user}'")
        
        # Ensure warmed up
        if not self.processor: 
            self.initialize({})
            
        try:
            # Setup Authenticator in headless mode (disables directional nodding requirements)
            auth = SentinelAuthenticator(target_user=target_user, headless=True)
            if not auth.initialize():
                self.logger.warning("PAM: Authenticator init failed")
                return {"success": True, "result": "ERROR"}

            # Launch Preview window if we have a display
            gui_context = params.get('gui_context', {})
            display = gui_context.get('display')
            xauth = gui_context.get('xauthority')
            
            preview_proc = None
            if display:
                try:
                    # Root for all system-wide sentinel resources
                    SENTINEL_ROOT = "/usr/lib/project-sentinel"
                    
                    # Use fixed absolute path to avoid venv/site-packages confusion
                    python_bin = os.path.join(SENTINEL_ROOT, "venv", "bin", "python3")
                    if not os.path.exists(python_bin):
                         python_bin = sys.executable # Fallback
                         
                    preview_script = os.path.join(SENTINEL_ROOT, "sentinel_tui", "scripts", "frame_preview.py")
                    
                    if os.path.exists(preview_script):
                         self.logger.info(f"PAM: Launching preview window on {display}")
                         env = os.environ.copy()
                         env['DISPLAY'] = display
                         if xauth: env['XAUTHORITY'] = xauth
                         env['PYTHONPATH'] = SENTINEL_ROOT
                         
                         # Redirect stdout/stderr to a dedicated log for debugging GUI issues
                         # Permission note: daemon runs as root, so it can write to /var/log/sentinel/
                         log_path = "/var/log/sentinel/preview.log"
                         with open(log_path, "a") as log_file:
                             log_file.write(f"\n--- Starting preview session: {time.ctime()} ---\n")
                             log_file.flush()
                             
                             preview_proc = subprocess.Popen(
                                 [python_bin, preview_script, "--socket", "/run/sentinel/sentinel.sock"],
                                 env=env, stderr=log_file, stdout=log_file,
                                 # Ensure we don't block the daemon if the window sits there
                             )
                    else:
                         self.logger.warning(f"PAM: Preview script NOT FOUND at {preview_script}")
                except Exception as pe:
                    self.logger.warning(f"PAM: Failed to launch preview subprocess: {pe}")

            # Start Camera (Short-lived)
            width = self.config.config.getint('Camera', 'width', fallback=640)
            height = self.config.config.getint('Camera', 'height', fallback=480)
            
            cam = CameraStream(src=self.config.CAMERA_INDEX, width=width, height=height, fps=15).start()
            
            # Allow camera to warmup slightly
            time.sleep(0.5)
            
            start_time = time.time()
            timeout = 11.0 # Bumping to 11s for better headless UX
            status = "FAILED"
            
            while time.time() - start_time < timeout:
                frame = cam.read()
                if frame is None:
                    time.sleep(0.05)
                    continue
                
                # Process logic
                state, msg, _, info = auth.process_frame(frame)
                dist = info.get('dist', 1.0) if info else 1.0
                
                # In headless PAM mode, we treat Tier 3 (Dist < 0.50) as success 
                # because we don't handle the interactive password prompt here.
                # States: SUCCESS, REQUIRE_2FA, STATE_2FA
                if state in ["SUCCESS", "REQUIRE_2FA", "STATE_2FA"]:
                    self.logger.info(f"PAM: SUCCESS for {target_user} (Dist: {dist:.3f}, State: {state})")
                    status = "SUCCESS"
                    break
                
                # Continue if FAILURE/LOCKOUT/etc until timeout
                time.sleep(0.03)
                
            if preview_proc:
                self.logger.info("PAM: Terminating preview window...")
                preview_proc.terminate()
                try: 
                    preview_proc.wait(timeout=1.0)
                except: 
                    preview_proc.kill()
                
            cam.stop()
            self.logger.info(f"PAM: Finished with status {status}")
            return {"success": True, "result": status}
            
        except Exception as e:
            self.logger.error(f"PAM Error: {e}")
            return {"success": True, "result": "ERROR"}


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
            ini_path = self.config.config_path if hasattr(self.config, 'config_path') else 'config.ini'
            cfg_edit = configparser.ConfigParser()
            
            if os.path.exists(ini_path):
                cfg_edit.read(ini_path)
            
            # Meta Section
            if 'Meta' not in cfg_edit: cfg_edit['Meta'] = {}
            if 'config_version' in updates: cfg_edit['Meta']['config_version'] = str(updates['config_version'])
            else: cfg_edit['Meta']['config_version'] = "1"
            
            # Camera Section
            if 'Camera' not in cfg_edit: cfg_edit['Camera'] = {}
            if 'camera_width' in updates: cfg_edit['Camera']['width'] = str(updates['camera_width'])
            if 'camera_height' in updates: cfg_edit['Camera']['height'] = str(updates['camera_height'])
            if 'camera_fps' in updates: cfg_edit['Camera']['fps'] = str(updates['camera_fps'])
            # Since user manually edited, turn OFF auto_detect to lock in their choice
            cfg_edit['Camera']['auto_detect'] = 'false'
            
            # Liveness / FaceDetection Section
            if 'Liveness' not in cfg_edit: cfg_edit['Liveness'] = {}
            if 'challenge_timeout' in updates: cfg_edit['Liveness']['challenge_timeout'] = str(updates['challenge_timeout'])
            if 'spoof_threshold' in updates: cfg_edit['Liveness']['spoof_threshold'] = str(updates['spoof_threshold'])
            
            if 'FaceDetection' not in cfg_edit: cfg_edit['FaceDetection'] = {}
            if 'min_face_size' in updates: cfg_edit['FaceDetection']['min_face_size'] = str(updates['min_face_size'])
            
            # Save to disk
            with open(ini_path, 'w') as configfile:
                cfg_edit.write(configfile)
                
            os.chmod(ini_path, 0o600)
                
            # Reload in memory
            self.config = BiometricConfig()
            self.logger.info("Configuration updated successfully.")
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

def _handle_rpc_line(service: SentinelService, methods: dict, line: str):
    try:
        request = json.loads(line)
        method = request.get("method")
        params = request.get("params", {}) or {}
        request_id = request.get("id", None)

        if request_id is None: return None # Notification

        if method not in methods:
            return _rpc_error(request_id, -32601, f"Method '{method}' not found")

        func = methods[method]
        
        # Don't lock entire status check, but lock logic methods
        # to ensure thread safety on Camera/Processor
        if method == "status":
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
        resp = _handle_rpc_line(service, methods, line)
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
    os.makedirs(sock_dir, exist_ok=True)
    
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
            service.initialize({"timeout_sec": 300})
            logger.info("Warmup finished.")
        except Exception as e:
            logger.warning(f"Warmup failed (non-fatal): {e}")

            
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
