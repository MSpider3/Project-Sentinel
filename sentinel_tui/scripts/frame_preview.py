"""
frame_preview.py — Live camera preview for active auth/enrollment sessions.

Reads base64-encoded frames streamed by the daemon via IPC (process_auth_frame
or process_enroll_frame) and displays them in a real OpenCV window.

This is launched as a subprocess during auth/enrollment so the TUI terminal
does not need to render pixels — the native window handles it.

Usage (internal):
    uv run python sentinel_tui/scripts/frame_preview.py --mode auth|enroll
"""

import argparse
import base64
import signal
import sys
import time

import cv2
import numpy as np

try:
    from sentinel_tui.constants import DEFAULT_SOCKET_PATH, IPC_AUTH_TIMEOUT, IPC_ENROLL_TIMEOUT
    from sentinel_tui.services.ipc_client import SentinelIPCClient
except ImportError:
    DEFAULT_SOCKET_PATH = "/run/sentinel/sentinel.sock"
    IPC_AUTH_TIMEOUT = 10
    IPC_ENROLL_TIMEOUT = 30


def _signal_handler(sig, frame):
    cv2.destroyAllWindows()
    sys.exit(0)


def run_preview(mode: str, socket_path: str) -> None:
    signal.signal(signal.SIGINT,  _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    rpc_method = "process_auth_frame" if mode == "auth" else "process_enroll_frame"
    timeout    = IPC_AUTH_TIMEOUT if mode == "auth" else IPC_ENROLL_TIMEOUT
    win_title  = f"Sentinel — {'Authentication' if mode == 'auth' else 'Enrollment'} Live Preview"

    ipc = SentinelIPCClient(socket_path)
    if not ipc.connect():
        print(f"[frame_preview] Cannot connect to daemon at {socket_path}", file=sys.stderr)
        sys.exit(1)

    cv2.namedWindow(win_title, cv2.WINDOW_AUTOSIZE)
    print(f"[frame_preview] Showing {mode} preview. Close window or press Q to stop.")

    placeholder = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(placeholder, "Waiting for frames...", (120, 240),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 200, 200), 2)
    cv2.imshow(win_title, placeholder)

    while True:
        # Check if window was closed by user
        try:
            if cv2.getWindowProperty(win_title, cv2.WND_PROP_VISIBLE) < 1:
                break
        except cv2.error:
            break

        key = cv2.waitKey(1) & 0xFF
        if key in (ord('q'), ord('Q'), 27):
            break

        res = ipc.call(rpc_method, timeout=timeout)

        if not res.get("success"):
            # Daemon stopped the session — exit cleanly
            cv2.putText(placeholder, "Session ended.", (180, 240),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 100, 255), 2)
            cv2.imshow(win_title, placeholder)
            cv2.waitKey(1500)
            break

        frame_b64 = res.get("frame", "")
        if frame_b64:
            try:
                raw = base64.b64decode(frame_b64)
                arr = np.frombuffer(raw, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is not None:
                    # Overlay status text
                    state   = res.get("state", res.get("status", ""))
                    message = res.get("message", res.get("pose_info", {}).get("instruction", ""))
                    face_box = res.get("face_box")

                    if face_box and len(face_box) >= 4:
                        x, y, w, h = face_box[:4]
                        cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 136), 2)

                    # Status overlay
                    color = (0, 255, 136) if state in ("SUCCESS", "ready") else \
                            (0, 200, 255) if state in ("RECOGNIZED", "LIVENESS_CHALLENGE") else \
                            (0, 140, 255)
                    cv2.putText(frame, str(message)[:70], (10, 30),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                    cv2.putText(frame, f"State: {state}", (10, 60),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)

                    cv2.imshow(win_title, frame)
                    continue
            except Exception:
                pass

        # No frame yet — show placeholder
        cv2.imshow(win_title, placeholder)

    ipc.disconnect()
    cv2.destroyAllWindows()
    print("[frame_preview] Preview closed.")


def main():
    parser = argparse.ArgumentParser(description="Sentinel Frame Preview Subprocess")
    parser.add_argument("--mode",   choices=["auth", "enroll"], default="auth")
    parser.add_argument("--socket", default=DEFAULT_SOCKET_PATH)
    args = parser.parse_args()
    run_preview(args.mode, args.socket)


if __name__ == "__main__":
    main()
