"""
camera_preview.py — Standalone OpenCV camera testing window.

Launched externally from the TUI to verify camera positioning and lighting.
Features clean SIGTERM handling and basic face detection overlay through IPC.
"""

import argparse
import logging
import signal
import sys
import threading
import time

import cv2
import numpy as np

# We import the IPC client to draw bounding boxes if daemon is running
try:
    from sentinel_tui.constants import DEFAULT_SOCKET_PATH, IPC_PREVIEW_TIMEOUT
    from sentinel_tui.services.ipc_client import SentinelIPCClient
    HAS_IPC = True
except ImportError:
    HAS_IPC = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] preview — %(message)s")
logger = logging.getLogger("preview")

class CameraPreviewApp:
    def __init__(self, device_id: int, width: int, height: int, socket_path: str):
        self.device_id = device_id
        self.width = width
        self.height = height
        self.socket_path = socket_path
        
        self.cap = None
        self.running = True
        self.window_name = f"Sentinel Camera Preview (Device {device_id})"
        self.ipc = None
        self.last_bboxes = []
        self.lock = threading.Lock()
        
        # Setup signal handlers for clean exit when TUI kills us
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, sig, frame):
        logger.info(f"Received signal {sig}, initiating clean shutdown...")
        self.running = False

    def start(self):
        logger.info(f"Opening camera {self.device_id} at {self.width}x{self.height}")
        self.cap = cv2.VideoCapture(self.device_id)
        
        if not self.cap.isOpened():
            logger.error(f"Failed to open camera /dev/video{self.device_id}")
            sys.exit(1)
            
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        
        actual_w = self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        actual_h = self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        logger.info(f"Camera opened. Actual resolution: {int(actual_w)}x{int(actual_h)}")

        # Connect IPC if requested
        if HAS_IPC and self.socket_path:
            self.ipc = SentinelIPCClient(self.socket_path)
            if self.ipc.connect():
                logger.info("Connected to daemon IPC for bounding boxes.")
                # We start a background thread to poll bounding boxes
                threading.Thread(target=self._ipc_worker, daemon=True).start()
            else:
                logger.warning("Could not connect to daemon. Running stream without overlay.")

        self._render_loop()

    def _render_loop(self):
        cv2.namedWindow(self.window_name, cv2.WINDOW_AUTOSIZE)
        
        logger.info("Starting frame render loop. Press 'q' or ESC to exit.")
        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                logger.warning("Failed to grab frame. Retrying...")
                time.sleep(0.5)
                continue
                
            # Draw UI
            display_frame = frame.copy()
            
            # Draw bounding boxes from IPC
            with self.lock:
                for box in self.last_bboxes:
                    x1, y1, x2, y2 = box
                    cv2.rectangle(display_frame, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
                    cv2.putText(display_frame, "Face", (int(x1), int(y1)-10), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
            
            # Overlay instructions
            cv2.putText(display_frame, "Press 'q' to close", (10, 30), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
            
            cv2.imshow(self.window_name, display_frame)
            
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q') or key == 27:  # 27 is ESC
                self.running = False
                
            # Check if window was closed via OS 'X' button
            if cv2.getWindowProperty(self.window_name, cv2.WND_PROP_VISIBLE) < 1:
                self.running = False

        self._cleanup()

    def _ipc_worker(self):
        """Poll daemon occasionally to verify if a face is visible"""
        while self.running and self.ipc and self.ipc.is_connected():
            res = self.ipc.call("process_auth_frame", timeout=IPC_PREVIEW_TIMEOUT)
            
            # Note: The daemon process_auth_frame currently expects an active authentication session.
            # If we just want raw BBox, we might need a dedicated `get_camera_frame` RPC.
            # For now, we attempt to read any returned bounding box.
            if res and res.get("success"):
                box = res.get("bounding_box", [])
                with self.lock:
                    self.last_bboxes = [box] if box else []
            time.sleep(0.2)
            
    def _cleanup(self):
        logger.info("Cleaning up camera resources...")
        if self.ipc:
            self.ipc.disconnect()
        if self.cap:
            self.cap.release()
        cv2.destroyAllWindows()
        logger.info("Preview closed cleanly.")

def main():
    parser = argparse.ArgumentParser(description="Sentinel Camera Preview Subprocess")
    parser.add_argument("--device-id", type=int, default=0, help="Camera device index")
    parser.add_argument("--width", type=int, default=640, help="Window width")
    parser.add_argument("--height", type=int, default=480, help="Window height")
    parser.add_argument("--socket", type=str, default="", help="Daemon socket path for overlays")
    
    args = parser.parse_args()
    
    app = CameraPreviewApp(
        device_id=args.device_id,
        width=args.width,
        height=args.height,
        socket_path=args.socket
    )
    
    app.start()

if __name__ == "__main__":
    main()
