# spoof_detector.py
import cv2
import time
import json
import os
import numpy as np
import onnxruntime as ort
import logging

# Use sentinel_logger if available (production), fall back silently for tests
try:
    import sentinel_logger as _slog
    logger = _slog.get("spoof_detector")
except ImportError:
    logger = logging.getLogger(__name__)

CFG_PATH = "models/minifas_calib.json"

def softmax(x):
    """Compute softmax values for each sets of scores in x."""
    x = x - np.max(x, axis=1, keepdims=True)
    e = np.exp(x)
    return e / (np.sum(e, axis=1, keepdims=True) + 1e-9)

class SpoofDetector:
    def __init__(self, model_path="models/MiniFASNetV2.onnx", thr=0.85):
        self.sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        self.inp = self.sess.get_inputs()[0].name
        self.cfg_path = CFG_PATH
        
        # Default config
        # Default to the PASSED threshold (which comes from config.ini)
        self.cfg = {"use_rgb": False, "live_idx": 0, "thr": float(thr), "calibrated": False}
        
        # Load saved calibration if it exists
        if os.path.exists(self.cfg_path):
            try:
                with open(self.cfg_path, "r") as f:
                    saved = json.load(f)
                    # ONLY load calibration data (rgb/live_idx), NOT the threshold.
                    # We want the threshold to be controlled by config.ini
                    if "use_rgb" in saved: self.cfg["use_rgb"] = saved["use_rgb"]
                    if "live_idx" in saved: self.cfg["live_idx"] = saved["live_idx"]
                    if "calibrated" in saved: self.cfg["calibrated"] = saved["calibrated"]
                    # Do NOT overwrite 'thr' with saved value
                logger.info(f"Loaded calibration. Using Threshold: {self.cfg['thr']}")
            except Exception as e:
                logger.warning(f"Could not load calibration file. Error: {e}")

        # --- NEW: Stateful Calibration Buffers ---
        self._combos = [(False, 0), (False, 1), (False, 2), (True, 0), (True, 1), (True, 2)]
        self._scores = {str(c): [] for c in self._combos}
        self._calib_samples = 0
        self._calib_target_samples = 80  # Collect ~80 frames of data (~3 seconds)
        self._calib_needed = not self.cfg.get("calibrated", False)

    def _square_crop(self, frame, bbox, scale=2.7, out_w=80, out_h=80):
        """A robust, simple, square cropping function."""
        x, y, w, h = bbox
        src_h, src_w, _ = frame.shape
        # Get center and side length
        center_x, center_y = x + w / 2.0, y + h / 2.0
        side = max(w, h) * float(scale)
        # Calculate top-left corner
        left, top = int(round(center_x - side / 2)), int(round(center_y - side / 2))
        # Clip to frame boundaries
        left, top = max(0, left), max(0, top)
        right, bottom = min(src_w - 1, int(round(left + side))), min(src_h - 1, int(round(top + side)))
        
        crop = frame[top:bottom, left:right]
        if crop.size == 0:
            return None
        return cv2.resize(crop, (out_w, out_h), interpolation=cv2.INTER_AREA)

    def _prep(self, img80, use_rgb=False):
        """Correct pre-processing with [-1, 1] normalization."""
        if use_rgb:
            img80 = cv2.cvtColor(img80, cv2.COLOR_BGR2RGB)
        
        x = img80.astype(np.float32) / 255.0
        x = (x - 0.5) / 0.5 # Normalize to [-1, 1] range
        
        x = np.transpose(x, (2, 0, 1))[None, ...].astype(np.float32)
        return x

    def _get_probs(self, x):
        """Runs inference and returns softmax probabilities."""
        logits = self.sess.run(None, {self.inp: x})[0]
        return softmax(logits)[0]

    def calibrate_tick(self, frame, bbox):
        """Called on every frame during calibration mode to collect data."""
        if not self._calib_needed:
            return

        crop = self._square_crop(frame, bbox, scale=2.7)
        if crop is None:
            return

        # Score all 6 possible configurations on the current frame
        for use_rgb, live_idx in self._combos:
            try:
                p = self._get_probs(self._prep(crop, use_rgb))
                self._scores[str((use_rgb, live_idx))].append(float(p[live_idx]))
            except:
                self._scores[str((use_rgb, live_idx))].append(0.0)
        
        self._calib_samples += 1

        # Once enough data is collected, find the best configuration
        if self._calib_samples >= self._calib_target_samples:
            logger.info("Finalizing calibration...")
            
            # --- CRITICAL FIX: Use highest MEDIAN score as the metric ---
            # This is robust to outliers and finds the most consistently high signal.
            best_combo_str = max(self._scores, key=lambda k: np.median(self._scores[k]) if self._scores[k] else -1)
            
            (use_rgb, live_idx) = eval(best_combo_str)
            self.cfg.update({"use_rgb": use_rgb, "live_idx": int(live_idx), "calibrated": True})
            
            os.makedirs(os.path.dirname(CFG_PATH), exist_ok=True)
            with open(self.cfg_path, "w") as f:
                json.dump(self.cfg, f)
            
            self._calib_needed = False
            logger.info(f"Calibration complete! Best settings found: {best_combo_str}")

    def is_calibrating(self):
        return self._calib_needed

    def predict(self, frame, bbox):
        """Main prediction API."""
        if self.is_calibrating():
            return (None, 0.0, {}) # Cannot predict while calibrating

        crop = self._square_crop(frame, bbox, scale=2.7)
        if crop is None:
            return (False, 0.0, {})
        
        probs = self._get_probs(self._prep(crop, self.cfg["use_rgb"]))
        live_conf = float(probs[self.cfg["live_idx"]])
        is_real = live_conf > self.cfg["thr"]
        
        # Convert NumPy array to native Python dict to avoid unpacking errors
        probs_dict = {"raw": str(probs), "live_conf": float(live_conf)}
        return (is_real, live_conf, probs_dict)