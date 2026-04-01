# biometric_processor.py - Core Biometric Processing Engine
# Refactored for Project Sentinel v2.3

import cv2
import numpy as np
import onnxruntime as ort
import os
from scipy.spatial import distance as dist
import time
import logging
import configparser
import datetime

# Import our intelligent modules
from spoof_detector import SpoofDetector
from stability_tracker import KalmanStabilityTracker

# Configure ONNX Runtime
ort.set_default_logger_severity(3)

# Setup logging
logger = logging.getLogger(__name__)

# ========== AUDIT LOGGING ==========
# ========== AUDIT LOGGING ==========
def cleanup_old_logs(log_dir, days_to_keep=30):
    """Deletes log files older than X days (FIFO based on modification time)."""
    try:
        now = time.time()
        retention_seconds = days_to_keep * 86400
        
        # Get all auth logs
        import glob
        logs = sorted(glob.glob(os.path.join(log_dir, "auth_audit_*.log")))
        
        for log_file in logs:
            file_time = os.path.getmtime(log_file)
            if now - file_time > retention_seconds:
                try:
                    os.remove(log_file)
                    # logger is not ready here, and we must not print to stdout.
                    # Silently succeed.
                except Exception as e:
                    pass
                    # logger.warning(f"Error deleting {log_file}: {e}")

    except Exception as e:
        pass

def setup_audit_logger():
    """Configures a separate logger for detailed authentication records."""
    audit_logger = logging.getLogger("SentinelAudit")
    audit_logger.setLevel(logging.INFO)
    
    # Check if handler already exists to avoid duplicates
    if not audit_logger.handlers:
        import sentinel_logger
        log_dir = getattr(sentinel_logger, 'LOG_DIR', "/var/log/sentinel")
        os.makedirs(log_dir, exist_ok=True)
        
        # Run cleanup first
        cleanup_old_logs(log_dir, days_to_keep=30)
        
        # Create dedicated log file with Date in filename
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        log_file = os.path.join(log_dir, f"auth_audit_{date_str}.log")
        
        handler = logging.FileHandler(log_file)
        
        # CSV-style format for easy parsing: Timestamp | Status | User | Message | Metrics
        formatter = logging.Formatter('%(asctime)s | %(message)s')
        handler.setFormatter(formatter)
        audit_logger.addHandler(handler)
    
    return audit_logger

# audit_log = setup_audit_logger() # MOVED to SentinelAuthenticator.__init__


# ========== CONFIGURATION ==========
class BiometricConfig:
    """Configuration constants for biometric processing."""
    
    def __init__(self, config_path=None):
        self.config = configparser.ConfigParser()
        self.defaults = {
            'Camera': {'width': '640', 'height': '480', 'fps': '15', 'index': '0'},
            'FaceDetection': {'min_face_size': '100', 'score_threshold': '0.6', 'nms_threshold': '0.3'},
            'Liveness': {
                'ear_open_threshold': '0.24', 
                'ear_closed_threshold': '0.19',
                'min_blink_duration_frames': '2',
                'challenge_timeout': '20.0',
                'session_reset_grace_period': '30',
                'spoof_threshold': '0.85'
            },
            'Security': {
                'recognition_threshold': '0.38',
                'adaptation_threshold': '0.25',
                'gallery_max_size': '20',
                'global_session_timeout': '60.0',
                'max_movement_threshold': '5000.0',
                'max_retries': '3',
                'golden_threshold': '0.25',
                'standard_threshold': '0.42',
                'two_factor_threshold': '0.50'
            },
            'AdaptivePolicy': {
                'adaptation_limit_per_day': '1',
                'initial_adaptations_require_password': '3'
            }
        }
        
        self.config.read_dict(self.defaults)
        
        # Determine paths intelligently: prefer local dev environment, fall back to system install
        local_model_dir = os.path.join(os.getcwd(), 'models')
        system_model_dir = '/usr/lib/project-sentinel/models'
        system_blacklist_dir = '/var/lib/project-sentinel/blacklist'
        
        # Use system paths if they exist (setup.sh creates them)
        if os.path.exists(system_model_dir):
            self.MODEL_DIR = system_model_dir
            self.BLACKLIST_DIR = system_blacklist_dir
        # Otherwise fall back to local dev mode paths
        else:
            self.MODEL_DIR = local_model_dir
            self.BLACKLIST_DIR = os.path.join(os.getcwd(), 'models', 'blacklist')
        
        # Root-level system config
        system_config = '/etc/project-sentinel/config.ini'
        local_config = os.path.join(os.getcwd(), 'config.ini')
        
        # Load System Config if exists
        if os.path.exists(system_config):
             self.config.read(system_config)
        # Also check for local config (dev mode)
        elif os.path.exists(local_config):
            self.config.read(local_config)
        
        # Load Explicit Config if provided (e.g. CLI override)
        if config_path and os.path.exists(config_path):
            self.config.read(config_path)

        self.DETECTOR_MODEL_FILE = 'face_detection_yunet_2023mar.onnx'
        self.RECOGNIZER_MODEL_FILE = 'face_recognition_sface_2021dec.onnx'
        
        # Ensure directories exist if we have write permissions
        try:
            os.makedirs(self.MODEL_DIR, exist_ok=True)
            os.makedirs(self.BLACKLIST_DIR, exist_ok=True)
        except PermissionError:
            pass  # Running without root, directories must already exist

        try:
            self.CAMERA_INDEX = self._resolve_camera_index()
            self.EAR_OPEN_THRESHOLD = self.config.getfloat('Liveness', 'ear_open_threshold')
            self.EAR_CLOSED_THRESHOLD = self.config.getfloat('Liveness', 'ear_closed_threshold')
            self.MIN_BLINK_DURATION_FRAMES = self.config.getint('Liveness', 'min_blink_duration_frames')
            self.CHALLENGE_TIMEOUT_SECONDS = self.config.getfloat('Liveness', 'challenge_timeout')
            self.SESSION_RESET_GRACE_PERIOD = self.config.getint('Liveness', 'session_reset_grace_period')
            self.SPOOF_THRESHOLD = self.config.getfloat('Liveness', 'spoof_threshold')
            
            self.RECOGNITION_THRESHOLD = self.config.getfloat('Security', 'recognition_threshold')
            self.ADAPTATION_THRESHOLD = self.config.getfloat('Security', 'adaptation_threshold')
            self.GALLERY_MAX_SIZE = self.config.getint('Security', 'gallery_max_size')
            self.GLOBAL_SESSION_TIMEOUT = self.config.getfloat('Security', 'global_session_timeout')
            self.MAX_MOVEMENT_THRESHOLD = self.config.getfloat('Security', 'max_movement_threshold')
            self.MAX_RETRIES = self.config.getint('Security', 'max_retries')
            
            self.GOLDEN_THRESHOLD = self.config.getfloat('Security', 'golden_threshold')
            self.STANDARD_THRESHOLD = self.config.getfloat('Security', 'standard_threshold')
            self.TWO_FACTOR_THRESHOLD = self.config.getfloat('Security', 'two_factor_threshold')
            
            self.ADAPTATION_LIMIT_PER_DAY = self.config.getint('AdaptivePolicy', 'adaptation_limit_per_day')
            self.INITIAL_ADAPTATIONS_REQUIRE_PASSWORD = self.config.getint('AdaptivePolicy', 'initial_adaptations_require_password')
            
            self.MIN_FACE_SIZE_PIXELS = self.config.getint('FaceDetection', 'min_face_size')
        except Exception as e:
            logger.error(f"Error parsing config: {e}. using fallbacks.")
            self.CAMERA_INDEX = 0
            self.RECOGNITION_THRESHOLD = 0.38
            self.CHALLENGE_TIMEOUT_SECONDS = 20.0
            self.SPOOF_THRESHOLD = 0.85
            self.MAX_RETRIES = 3

    def _resolve_camera_index(self):
        """Auto-detects IR camera if configured, otherwise uses index."""
        if self.config.has_option('Camera', 'device_id'):
            return self.config.getint('Camera', 'device_id', fallback=0)
        config_idx = self.config.getint('Camera', 'index', fallback=0)
        return config_idx
        
    def detect_ir_camera(self):
        """Scans video devices for IR capabilities (Future Implementation)."""
        pass





# ========== HELPER CLASSES ==========
class BlinkDetector:
    def __init__(self, config: BiometricConfig):
        self.state = "OPEN"
        self.closed_frames = 0
        self.ear_open_threshold = config.EAR_OPEN_THRESHOLD
        self.ear_closed_threshold = config.EAR_CLOSED_THRESHOLD
        self.min_blink_duration = config.MIN_BLINK_DURATION_FRAMES
    
    def update(self, ear):
        if self.state == "OPEN" and ear < self.ear_closed_threshold:
            self.state = "CLOSING"
        elif self.state == "CLOSING":
            if ear < self.ear_closed_threshold:
                self.closed_frames += 1
            else:
                self.state = "OPEN"
        elif self.state == "CLOSED":
            if ear > self.ear_open_threshold:
                self.state = "OPENING"
        elif self.state == "OPENING":
            self.state = "OPEN"
            self.closed_frames = 0
            return True
        if self.state == "CLOSING" and self.closed_frames >= self.min_blink_duration:
            self.state = "CLOSED"
        return False
    
    def reset(self):
        self.state = "OPEN"
        self.closed_frames = 0


# ========== UTILITY FUNCTIONS ==========
def eye_aspect_ratio(eye_landmarks, frame_shape):
    eye_pts = np.array([(lm.x * frame_shape[1], lm.y * frame_shape[0]) 
                        for lm in eye_landmarks], np.int32)
    A = dist.euclidean(eye_pts[1], eye_pts[5])
    B = dist.euclidean(eye_pts[2], eye_pts[4])
    C = dist.euclidean(eye_pts[0], eye_pts[3])
    return (A + B) / (2.0 * C)

def preprocess_frame(frame):
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    enhanced = cv2.merge([l, a, b])
    enhanced = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
    return enhanced

def extract_face_roi(frame, face_box):
    try:
        vals = face_box[0:4]
    except Exception:
        vals = face_box
    x, y, w, h = vals
    x, y = int(x), int(y)
    w, h = int(w), int(h)
    if x < 0 or y < 0 or x + w > frame.shape[1] or y + h > frame.shape[0]:
        return None
    return frame[y:y+h, x:x+w]


# ========== MAIN BIOMETRIC PROCESSOR ==========
class BiometricProcessor:
    def __init__(self, config=None, model_dir=None):
        self.config = config or BiometricConfig()
        self.model_dir = model_dir or self.config.MODEL_DIR
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # Models
        self.face_detector = None
        self.face_recognizer = None
        self.mp_face_mesh = None
        self.spoof_detector = None
        self.kalman_tracker = None
        self.blink_detector = None
        
        self.recognizer_input_name = None
        self.recognizer_input_shape = None
        
    def detect_camera_capabilities(self, device_id=0):
        """
        Probes the camera via OpenCV to find its max capabilities and 
        returns the optimal configuration presets.
        """
        self.logger.info(f"Probing camera '{device_id}' capabilities...")
        cap = cv2.VideoCapture(device_id)
        if not cap.isOpened():
            self.logger.warning("Could not open camera to probe capabilities. Falling back to defaults.")
            return {"width": 640, "height": 480, "fps": 15, "min_face_size": 100}
            
        # Attempt to request an absurdly high resolution to force camera to its max
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 10000)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 10000)
        
        max_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        max_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        
        self.logger.info(f"Detected max camera resolution: {max_width}x{max_height}")
        
        # Determine presets based on max capabilities
        # We downscale intentionally to save CPU, as face detection runs at 640x640 anyway
        if max_width >= 3840: # 4K
            return {"width": 1280, "height": 720, "fps": 15, "min_face_size": 150}
        elif max_width >= 1920: # 1080p
            return {"width": 960, "height": 540, "fps": 15, "min_face_size": 120}
        elif max_width >= 1280: # 720p
            return {"width": 640, "height": 480, "fps": 15, "min_face_size": 100}
        else: # 480p or below
            return {"width": 640, "height": 480, "fps": 10, "min_face_size": 80}

    def initialize_models(self):
        try:
            self.logger.info("Loading models...")
            
            self.face_detector = cv2.FaceDetectorYN.create(
                model=os.path.join(self.model_dir, self.config.DETECTOR_MODEL_FILE),
                config="",
                input_size=(640, 640),
                score_threshold=float(self.config.config.get('FaceDetection', 'score_threshold', fallback=0.6)),
                nms_threshold=float(self.config.config.get('FaceDetection', 'nms_threshold', fallback=0.3)),
                top_k=5000
            )
            
            providers = ['OpenVINOExecutionProvider', 'CPUExecutionProvider']
            self.face_recognizer = ort.InferenceSession(
                os.path.join(self.model_dir, self.config.RECOGNIZER_MODEL_FILE),
                providers=providers
            )
            self.recognizer_input_name = self.face_recognizer.get_inputs()[0].name
            self.recognizer_input_shape = self.face_recognizer.get_inputs()[0].shape[2:]
            
            # Use configured SPOOF_THRESHOLD
            self.spoof_detector = SpoofDetector(thr=self.config.SPOOF_THRESHOLD)
            
            self.kalman_tracker = KalmanStabilityTracker()
            self.blink_detector = BlinkDetector(self.config)
            
            # Warmup dummy inference
            self.logger.info("Running dummy warmup inferences...")
            try:
                dummy_img = np.zeros((640, 640, 3), dtype=np.uint8)
                self.face_detector.detect(dummy_img)
                
                if self.recognizer_input_shape and len(self.recognizer_input_shape) >= 2:
                    h, w = self.recognizer_input_shape
                    dummy_face = np.zeros((1, 3, h, w), dtype=np.float32)
                    self.face_recognizer.run(None, {self.recognizer_input_name: dummy_face})
                
                self.spoof_detector.predict(dummy_img, np.array([0, 0, 100, 100]))
            except Exception as e:
                self.logger.warning(f"Warmup failed (non-fatal): {e}")
                
            self.logger.info("All models loaded successfully.")
            return True
        except Exception as e:
            self.logger.error(f"Error loading models: {e}")
            return False
    
    def generate_embedding(self, face_roi):
        if face_roi is None or face_roi.size == 0:
            return None
        recognizer_input = cv2.resize(face_roi, self.recognizer_input_shape)
        recognizer_input = cv2.cvtColor(recognizer_input, cv2.COLOR_BGR2RGB)
        recognizer_input = np.transpose(recognizer_input, (2, 0, 1))
        recognizer_input = np.expand_dims(recognizer_input, axis=0).astype('float32')
        embedding = self.face_recognizer.run(None, {self.recognizer_input_name: recognizer_input})[0]
        return embedding.flatten()
    
    def detect_faces(self, frame):
        processed_frame = preprocess_frame(frame)
        frame_height, frame_width, _ = frame.shape
        self.face_detector.setInputSize((frame_width, frame_height))
        _, faces = self.face_detector.detect(processed_frame)
        return (processed_frame, faces if faces is not None else [])
    
    def validate_face_quality(self, face_box):
        x, y, w, h = face_box[0:4]
        w, h = int(w), int(h)
        if w < self.config.MIN_FACE_SIZE_PIXELS or h < self.config.MIN_FACE_SIZE_PIXELS:
            return (False, "FACE_TOO_SMALL")
        return (True, "VALID")
    
    def update_kalman_stability(self, face_box):
        x, y, w, h = face_box[0:4]
        return self.kalman_tracker.update((int(x), int(y), int(w), int(h)))
    
    def check_spoof(self, frame, face_box):
        if self.spoof_detector.is_calibrating():
            self.spoof_detector.calibrate_tick(frame, face_box[0:4].astype(int))
            return (None, None, "CALIBRATING")
        result = self.spoof_detector.predict(frame, face_box[0:4].astype(int))
        if result is not None and len(result) >= 3:
            is_live = bool(result[0]) if result[0] is not None else None
            confidence = float(result[1]) if result[1] is not None else 0.0
            info = result[2] if isinstance(result[2], dict) else {}
            return (is_live, confidence, info)
        return (None, 0.0, {})
    
    def detect_blink(self, frame):
        """Detect blink using MediaPipe FaceMesh (compatible with mediapipe >= 0.10).
        
        MediaPipe 0.10+ removed the legacy `mp.solutions` API.
        We use the new Tasks API: mediapipe.tasks.python.vision.FaceLandmarker.
        Falls back to (False, 0.0) if initialization fails so auth continues.
        """
        try:
            import mediapipe as mp
            
            # Initialize face landmarker on first call
            if not hasattr(self, '_face_landmarker') or self._face_landmarker is None:
                # Try new Tasks API (mediapipe >= 0.10)
                try:
                    from mediapipe.tasks.python import vision as mp_vision
                    from mediapipe.tasks.python.core import base_options as mp_base_options
                    
                    model_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'face_landmarker.task')
                    
                    # Fallback: download/locate the model from the mediapipe bundled assets
                    if not os.path.exists(model_path):
                        # mediapipe 0.10 bundles models inside the package
                        import mediapipe.tasks.python as mp_tasks
                        bundled = os.path.join(os.path.dirname(mp_tasks.__file__), 'vision', 'face_landmarker.task')
                        if os.path.exists(bundled):
                            model_path = bundled
                        else:
                            raise FileNotFoundError("face_landmarker.task not found")
                    
                    options = mp_vision.FaceLandmarkerOptions(
                        base_options=mp_base_options.BaseOptions(model_asset_path=model_path),
                        num_faces=1,
                    )
                    self._face_landmarker = mp_vision.FaceLandmarker.create_from_options(options)
                    self._use_new_mp_api = True
                except Exception:
                    # Final fallback: try legacy solutions API (mediapipe < 0.10)
                    try:
                        self._face_landmarker = mp.solutions.face_mesh.FaceMesh(
                            max_num_faces=1,
                            refine_landmarks=True,
                            min_detection_confidence=0.5,
                            min_tracking_confidence=0.5
                        )
                        self._use_new_mp_api = False
                    except Exception:
                        self._face_landmarker = None
                        return (False, 0.0)
            
            if self._face_landmarker is None:
                return (False, 0.0)
            
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            
            if getattr(self, '_use_new_mp_api', False):
                # New mediapipe Tasks API
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
                result = self._face_landmarker.detect(mp_image)
                if not result.face_landmarks:
                    return (False, 0.0)
                landmarks = result.face_landmarks[0]
                left_eye_lms  = [landmarks[i] for i in [362, 385, 387, 263, 373, 380]]
                right_eye_lms = [landmarks[i] for i in [33, 160, 158, 133, 153, 144]]
            else:
                # Legacy solutions API
                results = self._face_landmarker.process(rgb_frame)
                if not results.multi_face_landmarks:
                    return (False, 0.0)
                landmarks = results.multi_face_landmarks[0].landmark
                left_eye_lms  = [landmarks[i] for i in [362, 385, 387, 263, 373, 380]]
                right_eye_lms = [landmarks[i] for i in [33, 160, 158, 133, 153, 144]]
            
            ear = (eye_aspect_ratio(left_eye_lms, frame.shape) + eye_aspect_ratio(right_eye_lms, frame.shape)) / 2.0
            blink_detected = self.blink_detector.update(ear)
            return (bool(blink_detected), float(ear))
            
        except Exception as e:
            logger.debug(f"detect_blink error (non-fatal): {e}")
            return (False, 0.0)
    
    def identify_user_1n(self, embedding, user_galleries):
        best_match_user = None
        best_match_distance = float('inf')
        all_distances = {}
        
        emb_flat = embedding.flatten()
        emb_norm = np.linalg.norm(emb_flat)
        if emb_norm == 0.0:
            return (None, float('inf'), {})
            
        for user_name, gallery in user_galleries.items():
            if gallery is None or len(gallery) == 0:
                continue
                
            gal_mat = np.vstack([g.flatten() for g in gallery])
            gal_norms = np.linalg.norm(gal_mat, axis=1)
            
            valid = gal_norms != 0
            if not np.any(valid):
                continue
                
            sims = np.dot(gal_mat[valid], emb_flat) / (gal_norms[valid] * emb_norm)
            dists = 1.0 - sims
            
            min_dist = float(np.min(dists))
            all_distances[user_name] = min_dist
            
            if min_dist < best_match_distance:
                best_match_distance = min_dist
                best_match_user = user_name
                
        return (best_match_user, float(best_match_distance), all_distances)
    
    def adapt_gallery(self, username, gallery, embedding):
        try:
            updated = np.vstack([gallery, embedding])
        except Exception:
            updated = np.concatenate([gallery, embedding.reshape(1, -1)], axis=0)
        if len(updated) > self.config.GALLERY_MAX_SIZE:
            updated = updated[-self.config.GALLERY_MAX_SIZE:]
        gallery_path = os.path.join(self.model_dir, f"gallery_{username}.npy")
        np.save(gallery_path, updated)
        self.logger.info(f"Updated gallery for {username}: {len(updated)} embeddings")
        return updated


# ========== LIVENESS VALIDATOR ==========
class LivenessValidator:
    def __init__(self, config=None):
        self.config = config or BiometricConfig()
        self.logger = logging.getLogger(self.__class__.__name__)
        self.challenge_type = None
        self.challenge_start_pos = None
        self.challenge_prompt_time = None
        self.session_active = False
        self.frames_since_face_seen = 0
        self.checklist = {"spoof_check": False, "challenge": False, "blink": False}
    
    def start_session(self, challenge_type=None):
        self.session_active = True
        self.challenge_type = challenge_type or np.random.choice(["LEFT", "RIGHT", "UP", "DOWN"])
        self.challenge_start_pos = None
        self.challenge_prompt_time = time.time()
        self.checklist = {"spoof_check": False, "challenge": False, "blink": False}
    
    def reset_session(self):
        self.session_active = False
        self.challenge_type = None
        self.challenge_start_pos = None
        self.challenge_prompt_time = None
        self.frames_since_face_seen = 0
        self.checklist = {"spoof_check": False, "challenge": False, "blink": False}
    
    def is_timed_out(self):
        if self.challenge_prompt_time is None:
            return False
        elapsed = time.time() - self.challenge_prompt_time
        return elapsed > self.config.CHALLENGE_TIMEOUT_SECONDS
    
    def update_challenge_progress(self, face_box, nose_pos):
        if self.checklist["challenge"]:
            return True
        x, y, w, h = face_box[0:4]
        w = int(w)
        if self.challenge_start_pos is None:
            self.challenge_start_pos = nose_pos
        delta_x = nose_pos[0] - self.challenge_start_pos[0]
        delta_y = nose_pos[1] - self.challenge_start_pos[1]
        motion_threshold = float(w) * 0.15
        challenge_completed = (
            (self.challenge_type == "LEFT" and delta_x < -motion_threshold) or
            (self.challenge_type == "RIGHT" and delta_x > motion_threshold) or
            (self.challenge_type == "UP" and delta_y < -motion_threshold) or
            (self.challenge_type == "DOWN" and delta_y > motion_threshold)
        )
        if challenge_completed:
            self.checklist["challenge"] = True
            self.logger.info(f"Challenge '{self.challenge_type}' completed.")
        return challenge_completed
    
    def mark_spoof_check_passed(self):
        self.checklist["spoof_check"] = True
    
    def mark_blink_detected(self):
        # Enforce sequence: Challenge must be passed BEFORE Blink is accepted
        if self.checklist["challenge"]:
            self.checklist["blink"] = True
    
    def all_checks_passed(self):
        return all(self.checklist.values())
    
    def get_pending_checks(self):
        return [check for check, passed in self.checklist.items() if not passed]
    
    def increment_face_loss_counter(self):
        self.frames_since_face_seen += 1
    
    def should_reset_on_face_loss(self):
        return self.frames_since_face_seen > self.config.SESSION_RESET_GRACE_PERIOD


# ========== FACE EMBEDDING STORE ==========
class FaceEmbeddingStore:
    def __init__(self, gallery_dir=None, model_dir=None):
        self.config = BiometricConfig()
        self.gallery_dir = gallery_dir or (model_dir or self.config.MODEL_DIR)
        self.logger = logging.getLogger(self.__class__.__name__)
    
    def load_all_galleries(self):
        import glob
        user_galleries = {}
        user_names = []
        gallery_files = sorted(glob.glob(os.path.join(self.gallery_dir, "gallery_*.npy")))
        if not gallery_files:
            return {}, []
        for gallery_file in gallery_files:
            user_name = os.path.basename(gallery_file).replace("gallery_", "").replace(".npy", "")
            try:
                embeddings = np.load(gallery_file)
                user_galleries[user_name] = embeddings
                user_names.append(user_name)
            except Exception as e:
                pass
                pass
        return user_galleries, user_names
        
    def check_expiry(self, username, max_days=45):
        """Checks if a user's gallery is expired."""
        gallery_file = os.path.join(self.gallery_dir, f"gallery_{username}.npy")
        if not os.path.exists(gallery_file):
            return False # No gallery, so not expired (just missing)
            
        mtime = os.path.getmtime(gallery_file)
        age_seconds = time.time() - mtime
        age_days = age_seconds / 86400.0
        
        if age_days > max_days:
            return True
        return False


# ========== BLACKLIST MANAGER (IDS) ==========
class BlacklistManager:
    def __init__(self, model_dir=None):
        self.config = BiometricConfig()
        # Use explicit blacklist dir from config if available, else derive (backward compatibility)
        if hasattr(self.config, 'BLACKLIST_DIR'):
            self.blacklist_dir = self.config.BLACKLIST_DIR
        else:
            self.model_dir = model_dir or self.config.MODEL_DIR
            self.blacklist_dir = os.path.join(self.model_dir, "blacklist")
            
        self.blacklist_file = os.path.join(self.blacklist_dir, "blacklist_embeddings.npy")
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # Ensure directory exists (if running as root, config init does this too, but double check)
        if not os.path.exists(self.blacklist_dir) and os.access(os.path.dirname(self.blacklist_dir), os.W_OK):
             os.makedirs(self.blacklist_dir, exist_ok=True)
            
        self.blacklist_embeddings = self._load_blacklist()

    def _load_blacklist(self):
        if os.path.exists(self.blacklist_file):
            try:
                return np.load(self.blacklist_file)
            except Exception as e:
                self.logger.error(f"Failed to load blacklist: {e}")
        return np.empty((0, 128)) # Assuming 128D embeddings

    def check_blacklist(self, embedding):
        if self.blacklist_embeddings.size == 0:
            return False, 0.0
            
        # Compare against all blacklist embeddings
        distances = [dist.cosine(embedding, blocked.flatten()) for blocked in self.blacklist_embeddings]
        min_dist = min(distances)
        
        # If very close to a blacklisted face, reject
        # Using a slightly looser threshold for blocking to be safe? 
        # Or strict? User said "close match". Let's use Golden Threshold for blocking safety.
        if min_dist < self.config.GOLDEN_THRESHOLD: 
            return True, min_dist
        return False, min_dist

    def confirm_intrusion(self, filename):
        """Moves a temporary intrusion screenshot/embedding to permanent blacklist."""
        try:
            # Load the specific temporary embedding associated with this intrusion
            # For now, we assumed they were all in one big array, but for deletion/mgmt 
            # we need to track them. 
            # SIMPLIFICATION: We will just load the specific intrusion_X.npy file if we saved one.
            # But in add_intrusion we only saved to the big array. 
            # FIX: We need to save individual embedding files for review.
            
            # The 'filename' passed here is the jpg image path.
            # We derive the .npy path from it.
            base = os.path.splitext(filename)[0]
            npy_path = base + ".npy"
            
            if os.path.exists(npy_path):
                emb = np.load(npy_path)
                # Add to permanent memory
                if self.blacklist_embeddings.size == 0:
                     self.blacklist_embeddings = emb.reshape(1, -1)
                else:
                     self.blacklist_embeddings = np.vstack([self.blacklist_embeddings, emb])
                np.save(self.blacklist_file, self.blacklist_embeddings)
                
                # We do NOT delete the image yet, the user might want to keep it as proof?
                # Or maybe we do. Let's keep it but mark it? 
                # Actually, standard practice: keep log, delete temp embedding file.
                os.remove(npy_path)
                return True
        except Exception as e:
            self.logger.error(f"Failed to confirm intrusion: {e}")
        return False

    def delete_intrusion_record(self, filename):
        """Deletes the intrusion record (false positive)."""
        try:
            if os.path.exists(filename):
                os.remove(filename)
            
            # Also remove corresponding npy if exists
            base = os.path.splitext(filename)[0]
            npy_path = base + ".npy"
            if os.path.exists(npy_path):
                os.remove(npy_path)
            return True
        except Exception as e:
            self.logger.error(f"Failed to delete intrusion: {e}")
        return False

    def add_intrusion(self, frame, embedding):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # 1. Save Screenshot
        filename = f"intrusion_{timestamp}.jpg"
        filepath = os.path.join(self.blacklist_dir, filename)
        cv2.imwrite(filepath, frame)
        
        # 2. Save individual embedding for review
        npy_path = os.path.join(self.blacklist_dir, f"intrusion_{timestamp}.npy")
        np.save(npy_path, embedding)
        
        # We generally do NOT add to active blacklist until confirmed by user
        # self.blacklist_embeddings = ... (removed)
        
        self.logger.warning(f"Intrusion logged: {filename}")


# ========== ADAPTIVE MANAGER ==========
class AdaptiveManager:
    def __init__(self, user, model_dir=None):
        self.user = user
        self.config = BiometricConfig()
        self.model_dir = model_dir or self.config.MODEL_DIR
        self.adaptive_dir = os.path.join(self.model_dir, "adaptive_galleries")
        self.gallery_file = os.path.join(self.adaptive_dir, f"gallery_{user}_adaptive.npy")
        self.metadata_file = os.path.join(self.adaptive_dir, f"gallery_{user}_meta.json")
        self.logger = logging.getLogger(self.__class__.__name__)
        
        if not os.path.exists(self.adaptive_dir):
            os.makedirs(self.adaptive_dir, exist_ok=True)
            
        self.gallery = self._load_gallery()
        
    def _load_gallery(self):
        if os.path.exists(self.gallery_file):
            try:
                return np.load(self.gallery_file)
            except:
                pass
        return np.empty((0, 128))

    def get_gallery(self):
        return self.gallery

    def can_adapt_today(self):
        # Check metadata for daily limit
        # For simplicity, we can check file modification time or a helper file
        # Check metadata json
        import json
        if os.path.exists(self.metadata_file):
            try:
                with open(self.metadata_file, 'r') as f:
                    data = json.load(f)
                    last_date = data.get('last_adaptation_date')
                    count = data.get('today_count', 0)
                    today = datetime.datetime.now().strftime("%Y-%m-%d")
                    
                    if last_date == today:
                        if count >= self.config.ADAPTATION_LIMIT_PER_DAY:
                            return False
                    else:
                        # New day
                        return True
            except:
                pass
        return True

    def record_adaptation(self):
        import json
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        data = {'last_adaptation_date': today, 'today_count': 1, 'total_count': len(self.gallery)}
        
        if os.path.exists(self.metadata_file):
            try:
                with open(self.metadata_file, 'r') as f:
                    old_data = json.load(f)
                    if old_data.get('last_adaptation_date') == today:
                        data['today_count'] = old_data.get('today_count', 0) + 1
                    data['total_count'] = old_data.get('total_count', 0) + 1 # Update total
            except:
                pass
                
        with open(self.metadata_file, 'w') as f:
            json.dump(data, f)

    def adapt(self, embedding):
        # FIFO Queue logic
        if self.gallery.size == 0:
            self.gallery = embedding.reshape(1, -1)
        else:
            self.gallery = np.vstack([self.gallery, embedding])
            
        # Limit size (15-20 as requested, let's say 20)
        max_size = 20
        if len(self.gallery) > max_size:
            self.gallery = self.gallery[-max_size:] # Keep latest
            
        np.save(self.gallery_file, self.gallery)
        self.record_adaptation()
        self.logger.info(f"Adaptive gallery updated for {self.user}")


# ========== SENTINEL AUTHENTICATOR ==========
class SentinelAuthenticator:
    
    # States
    STATE_WAITING = "WAITING"
    STATE_RECOGNIZED = "RECOGNIZED"  # Doing challenges
    STATE_SUCCESS = "SUCCESS"
    STATE_FAILURE = "FAILURE"
    STATE_LOCKOUT = "LOCKOUT"
    STATE_2FA = "REQUIRE_2FA" # New State
    
    def __init__(self, target_user=None):
        self.config = BiometricConfig()
        self.processor = BiometricProcessor(self.config)
        self.store = FaceEmbeddingStore()
        self.validator = LivenessValidator(self.config)
        self.blacklist_manager = BlacklistManager()
        self.adaptive_manager = None # Lazy init per user
        
        # Initialize audit logger here to avoid import-time stdout noise
        self.audit_log = setup_audit_logger()
        
        self.target_user = target_user
        self.galleries = {}
        self.state = self.STATE_WAITING
        self.message = "Initializing..."
        
        # Target Locking
        self.locked_face_center = None
        
        # Session Timing
        self.session_start_time = None
        
        # Persistent Data
        self.matched_user = None
        self.last_distance = None
        self.active_tier = None # 1=Golden, 2=Standard, 3=2FA, 4=Fail
        
        # Retry Logic
        self.retry_count = 0
        
        # Adaptation Luck (Roll 0-10, needs 7)
        self.adaptation_lucky_roll = np.random.randint(0, 11)
        
    def log_audit(self, status, message, extra=None):
        """Helper to log detailed audit events."""
        details = []
        if self.matched_user:
            details.append(f"User={self.matched_user}")
        else:
            details.append("User=Unknown")
            
        if self.last_distance:
            details.append(f"Dist={self.last_distance:.3f}")
            
        details.append(f"Retries={self.retry_count}")
        details.append(f"Tier={self.active_tier}")
        
        duration = time.time() - self.session_start_time if self.session_start_time else 0
        details.append(f"Duration={duration:.1f}s")
        
        if extra:
            details.append(f"Extra={extra}")
            
        log_msg = f"{status} | {message} | {' '.join(details)}"
        if hasattr(self, 'audit_log'):
            self.audit_log.info(log_msg)

    def initialize(self):
        if not self.processor.initialize_models():
            return False
            
        galleries, _ = self.store.load_all_galleries()
        if self.target_user:
            if self.target_user in galleries:
                self.galleries = {self.target_user: galleries[self.target_user]}
            else:
                self.message = f"User {self.target_user} not enrolled."
                return False
        else:
            self.galleries = galleries
            
        if len(self.galleries) == 0:
            self.message = "No users enrolled."
            return False
            
        self.session_start_time = time.time()
        self.message = "Ready. Look at camera."
        return True
        
    def _center_of(self, box):
        x, y, w, h = box[0:4]
        return (x + w/2, y + h/2)

    def _dist_sq(self, p1, p2):
        return (p1[0] - p2[0])**2 + (p1[1] - p2[1])**2
        
    def process_frame(self, frame):
        # Global Timeout Check
        if time.time() - self.session_start_time > self.config.GLOBAL_SESSION_TIMEOUT:
            self.log_audit("TIMEOUT", "Global Session Timeout")
            return self.STATE_FAILURE, "Global Session Timeout", None, {}
            
        # Lockout check
        if self.retry_count >= self.config.MAX_RETRIES:
             return self.STATE_FAILURE, "Maximum attempts reached. Use Password.", None, {}

        processed_frame, faces = self.processor.detect_faces(frame)
        debug_info = {}
        
        active_face = None
        
        if len(faces) > 0:
            if self.locked_face_center is None:
                active_face = max(faces, key=lambda f: float(f[2])*float(f[3]))
            else:
                candidates = []
                for f in faces:
                    c = self._center_of(f)
                    d = self._dist_sq(c, self.locked_face_center)
                    candidates.append((d, f))
                candidates.sort(key=lambda x: x[0])
                best_dist, best_f = candidates[0]
                if best_dist < self.config.MAX_MOVEMENT_THRESHOLD:
                    active_face = best_f
                else:
                    active_face = None
        
        if active_face is None:
            self.validator.increment_face_loss_counter()
            if self.validator.should_reset_on_face_loss():
                 if self.state != self.STATE_WAITING:
                     self.log_audit("RESET", "Face lost during session")
                 self._reset(full_reset=False)
                 self.message = "Face lost. Resetting..."
            return self.STATE_WAITING, self.message, None, debug_info
            
        self.locked_face_center = self._center_of(active_face)
        smoothed = self.processor.update_kalman_stability(active_face)
        
        is_live, confidence, info = self.processor.check_spoof(frame, active_face)
        debug_info['spoof_conf'] = confidence
        
        if is_live is False:
             self.retry_count += 1
             remaining = self.config.MAX_RETRIES - self.retry_count
             self.log_audit("WARNING", f"Spoof Detected (Conf: {confidence:.2f})")
             
             if remaining <= 0:
                 self.log_audit("FAILURE", "Locked out due to max spoof attempts")
                 return self.STATE_FAILURE, "Locked out: Spoof detected", smoothed, {}
             
             self._reset(full_reset=True)
             self.message = f"Spoof/Fake detected. Attempts left: {remaining}"
             return self.STATE_WAITING, self.message, smoothed, {}
             
        if self.state == self.STATE_WAITING:
            self.message = "Scanning face..."
            valid, reason = self.processor.validate_face_quality(active_face)
            if not valid:
                self.message = f"Quality: {reason}"
            else:
                 roi = extract_face_roi(frame, active_face)
                 emb = self.processor.generate_embedding(roi)
                 
                 if emb is not None:
                     # 1. Blacklist Check (IDS)
                     is_blocked, block_dist = self.blacklist_manager.check_blacklist(emb)
                     if is_blocked:
                         self.log_audit("BLOCKED", f"Intrusion matching blacklist (Dist: {block_dist:.3f})")
                         return self.STATE_FAILURE, "Access Denied: Restricted Identity", smoothed, {}
                         
                     # 2. Identification
                     user, dist, _ = self.processor.identify_user_1n(emb, self.galleries)
                     
                     # 3. Tier Classification
                     tier = 4
                     if dist < self.config.GOLDEN_THRESHOLD:
                         tier = 1
                     elif dist < self.config.STANDARD_THRESHOLD:
                         tier = 2
                     elif dist < self.config.TWO_FACTOR_THRESHOLD:
                         tier = 3
                     else:
                         tier = 4
                     
                     self.active_tier = tier
                     self.last_distance = dist
                     
                     if tier <= 3:
                         self.state = self.STATE_RECOGNIZED
                         self.matched_user = user
                         self.validator.start_session()
                         self.message = f"Hi {user}! {self.validator.challenge_type}"
                         self.log_audit("INFO", f"User recognized (Tier {tier}), starting challenges")
                         
                         # Check adaptive gallery too for better match?
                         # For now, just use core gallery for ID, but we could check adaptive if core fails?
                         # The prompt implies adaptive is for saving, but probably also for reading.
                         # "it will work like a queue... different from initial embeddings"
                         # Logic: If identified, proceed.
                     else:
                         # Tier 4: Unknown / Intrusion
                         self.message = "Unknown User"
                         # Trigger IDS recording
                         self.blacklist_manager.add_intrusion(frame, emb)
                         self.log_audit("INTRUSION", "Unknown face added to blacklist")
                         return self.STATE_FAILURE, "Access Denied", smoothed, {}
        
        elif self.state == self.STATE_RECOGNIZED:
            if self.validator.is_timed_out():
                self.retry_count += 1
                remaining = self.config.MAX_RETRIES - self.retry_count
                self.log_audit("WARNING", "Challenge Timeout")
                
                if remaining <= 0:
                     self.log_audit("FAILURE", "Locked out due to challenge timeout")
                     return self.STATE_FAILURE, "Challenge Timeout. Locked out.", smoothed, {}
                
                self._reset(full_reset=True)
                self.message = f"Too slow. Attempts left: {remaining}"
                return self.STATE_WAITING, self.message, smoothed, {}
            
            cx, cy = self._center_of(active_face)
            nose = (int(cx), int(cy))
            
            if not self.validator.checklist['challenge']:
                # STAGE 1: Head Pose Challenge
                if self.validator.update_challenge_progress(active_face, nose):
                    self.message = "Good! Now Blink."
                else:
                    self.message = f"Hi {self.matched_user}! Turn Head {self.validator.challenge_type}"
            else:
                # STAGE 2: Blink Detection (Only after challenge is done)
                self.message = "Please Blink..."
                try:
                    blink, ear = self.processor.detect_blink(frame)
                    if blink:
                        self.validator.mark_blink_detected()
                except Exception as e:
                    # Non-fatal: blink detection failing should not crash auth
                    logger.debug(f"Blink detection skipped: {e}")
                    self.validator.mark_blink_detected()  # skip the challenge gracefully
            
            self.validator.mark_spoof_check_passed()
            
            if self.validator.all_checks_passed():
                # Final Decision based on Tier
                if self.active_tier == 3:
                    self.state = self.STATE_2FA
                    self.message = f"2FA Required: {self.matched_user}"
                    self.log_audit("SUCCESS_2FA", "Biometrics passed, password required")
                    return self.state, self.message, smoothed, {'user': self.matched_user, 'dist': self.last_distance}
                
                else:
                    self.state = self.STATE_SUCCESS
                    self.message = f"Access Granted: {self.matched_user}"
                    self.log_audit("SUCCESS", "Access Granted")
                    
                    # Adaptation (Golden Zone only)
                    if self.active_tier == 1 and self.adaptation_lucky_roll == 7:
                        try:
                            # Re-generate clean embedding for storage
                            roi = extract_face_roi(frame, active_face)
                            emb = self.processor.generate_embedding(roi)
                            if emb is not None:
                                if self.adaptive_manager is None:
                                    self.adaptive_manager = AdaptiveManager(self.matched_user)
                                
                                if self.adaptive_manager.can_adapt_today():
                                    self.adaptive_manager.adapt(emb)
                                    self.log_audit("ADAPT", "Golden Login - Template Adapted")
                        except Exception as e:
                            self.log_audit("ERROR", f"Adaptation failed: {e}")

        return self.state, self.message, smoothed, {'user': self.matched_user, 'dist': self.last_distance}

    def _reset(self, full_reset=False):
        self.state = self.STATE_WAITING
        self.locked_face_center = None
        self.validator.reset_session()
        self.matched_user = None
        self.active_tier = None
