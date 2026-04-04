"""
frame_preview.py — GStreamer-based live camera preview for PAM authentication.

Replaces the previous OpenCV window with a native GStreamer pipeline:
    appsrc (JPEG bytes via IPC) → jpegdec → videoconvert → autovideosink

Why GStreamer instead of cv2:
  - Fedora system Python is 3.14; opencv-python has no 3.14 PyPI wheel
  - gi.repository.Gst is always available on Fedora as a system package
  - autovideosink picks waylandsink/xvimagesink automatically
  - Runs with /usr/bin/python3 — no venv needed in the subprocess

Usage (called by sentinel daemon via runuser):
    python3 frame_preview.py --mode auth --socket /run/sentinel/sentinel.sock
"""

import argparse
import base64
import os
import signal
import sys
import threading
import time

# ── GStreamer bootstrap ───────────────────────────────────────────────────────
try:
    import gi
    gi.require_version('Gst', '1.0')
    gi.require_version('GLib', '2.0')
    from gi.repository import Gst, GLib
    HAS_GST = True
except (ImportError, ValueError) as _gst_err:
    print(f"[preview] GStreamer (gi.repository) not available: {_gst_err}", file=sys.stderr)
    HAS_GST = False

# ── IPC client bootstrap ──────────────────────────────────────────────────────
_SENTINEL_ROOT = os.environ.get('PYTHONPATH', '/usr/lib/project-sentinel').split(':')[0]
if _SENTINEL_ROOT and _SENTINEL_ROOT not in sys.path:
    sys.path.insert(0, _SENTINEL_ROOT)

try:
    from sentinel_tui.services.ipc_client import SentinelIPCClient
    from sentinel_tui.constants import DEFAULT_SOCKET_PATH
except ImportError:
    DEFAULT_SOCKET_PATH = os.environ.get('SENTINEL_SOCKET_PATH', '/run/sentinel/sentinel.sock')

    class SentinelIPCClient:
        """Minimal inline IPC client used when sentinel_tui is not importable."""
        def __init__(self, socket_path: str):
            import socket as _s, json as _j
            self._path = socket_path
            self._sock = None
            self._lock = threading.Lock()
            self._s = _s
            self._j = _j

        def connect(self) -> bool:
            try:
                self._sock = self._s.socket(self._s.AF_UNIX, self._s.SOCK_STREAM)
                self._sock.settimeout(15.0)
                self._sock.connect(self._path)
                return True
            except Exception as e:
                print(f"[preview] IPC connect failed: {e}", file=sys.stderr)
                return False

        def call(self, method: str, params: dict = None, timeout: float = 15.0) -> dict:
            req = self._j.dumps({"method": method, "params": params or {}, "id": 1}) + "\n"
            try:
                with self._lock:
                    self._sock.sendall(req.encode())
                    data = b""
                    while b"\n" not in data:
                        chunk = self._sock.recv(65536)
                        if not chunk:
                            break
                        data += chunk
                return self._j.loads(data.decode().strip()).get("result", {})
            except Exception as e:
                print(f"[preview] IPC call error: {e}", file=sys.stderr)
                return {"success": False, "error": str(e)}

        def disconnect(self):
            if self._sock:
                try:
                    self._sock.close()
                except Exception:
                    pass


# ── GStreamer preview implementation ──────────────────────────────────────────

class GStreamerPreview:
    """
    Displays JPEG frames from the Sentinel daemon in a native GStreamer window.

    Pipeline:
        appsrc → jpegdec → videoconvert → autovideosink
    """

    def __init__(self, mode: str, socket_path: str):
        self.mode        = mode
        self.socket_path = socket_path
        self.pipeline    = None
        self.appsrc      = None
        self.loop        = None
        self.ipc         = None
        self._running    = True
        self._pts        = 0      # nanosecond PTS counter
        self._frame_n    = 0

    # Pipeline -------------------------------------------------------------------

    def _build_pipeline(self):
        # autovideosink picks the best available output:
        #   Wayland → waylandsink
        #   X11     → xvimagesink / ximagesink
        pipeline_desc = (
            "appsrc name=src is-live=true block=false format=time "
            "    caps=image/jpeg,framerate=15/1 "
            "! jpegdec "
            "! videoconvert "
            "! autovideosink name=sink sync=false"
        )
        self.pipeline = Gst.parse_launch(pipeline_desc)
        self.appsrc   = self.pipeline.get_by_name("src")

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message)

    def _on_bus_message(self, bus, message):
        t = message.type
        if t == Gst.MessageType.EOS:
            print("[preview] Pipeline EOS.", file=sys.stderr)
            self._quit()
        elif t == Gst.MessageType.ERROR:
            err, dbg = message.parse_error()
            print(f"[preview] GStreamer error: {err} | {dbg}", file=sys.stderr)
            self._quit()

    # Frame pusher ---------------------------------------------------------------

    def _push_jpeg(self, jpeg_bytes: bytes, state: str = "", message: str = "", info=None, face_box=None):
        """Push JPEG to GStreamer pipeline, optionally with text overlay (guidance prompts)."""
        # Optionally overlay guidance text on frame
        if state or message:
            overlay_applied = False
            display_text = message if message else f"State: {state}"
            if info is None:
                info = {}
            
            # Try OpenCV first
            try:
                import cv2
                import numpy as np
                
                frame = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
                if frame is not None:
                    h, w = frame.shape[:2]
                    
                    if len(display_text) > 80:
                        display_text = display_text[:77] + "..."
                    
                    # If the service drew an old label above the face box, cover it.
                    if face_box is not None and len(face_box) >= 4:
                        try:
                            bx, by, bw, bh = [int(float(v)) for v in face_box[:4]]
                            label_top = max(0, by - 34)
                            cv2.rectangle(frame, (max(0, bx - 4), label_top), (min(w, bx + bw + 4), by), (0, 0, 0), -1)
                        except Exception:
                            pass
                    
                    # Draw prototype-style top guidance bar
                    overlay = frame.copy()
                    cv2.rectangle(overlay, (0, 0), (w, 72), (0, 0, 0), -1)
                    frame = cv2.addWeighted(overlay, 0.72, frame, 0.28, 0)
                    
                    font = cv2.FONT_HERSHEY_DUPLEX
                    text_color = (240, 220, 0)  # Yellow
                    accent_color = (0, 255, 0) if state in ("SUCCESS", "RECOGNIZED") else (0, 165, 255) if state in ("REQUIRE_2FA", "LIVENESS_CHALLENGE") else (0, 255, 255)
                    
                    cv2.putText(frame, display_text, (18, 42), font, 0.9, text_color, 2, cv2.LINE_AA)
                    cv2.line(frame, (0, 70), (w, 70), accent_color, 3)
                    
                    if face_box is not None and len(face_box) >= 4:
                        try:
                            bx, by, bw, bh = [int(float(v)) for v in face_box[:4]]
                            cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), accent_color, 2)
                            corner_len = max(15, min(bw, bh) // 8)
                            corner_thickness = 2
                            cv2.line(frame, (bx, by), (bx + corner_len, by), accent_color, corner_thickness)
                            cv2.line(frame, (bx, by), (bx, by + corner_len), accent_color, corner_thickness)
                            cv2.line(frame, (bx + bw, by), (bx + bw - corner_len, by), accent_color, corner_thickness)
                            cv2.line(frame, (bx + bw, by), (bx + bw, by + corner_len), accent_color, corner_thickness)
                            cv2.line(frame, (bx, by + bh), (bx + corner_len, by + bh), accent_color, corner_thickness)
                            cv2.line(frame, (bx, by + bh), (bx, by + bh - corner_len), accent_color, corner_thickness)
                            cv2.line(frame, (bx + bw, by + bh), (bx + bw - corner_len, by + bh), accent_color, corner_thickness)
                            cv2.line(frame, (bx + bw, by + bh), (bx + bw, by + bh - corner_len), accent_color, corner_thickness)
                        except Exception:
                            pass
                    
                    # Draw confidence score at the bottom right, if available
                    dist = info.get('dist')
                    if dist is not None and isinstance(dist, (int, float)):
                        confidence = max(0.0, min(100.0, (1.0 - max(0.0, min(1.0, float(dist)))) * 100.0))
                        conf_text = f"{confidence:.1f}%"
                        conf_size = cv2.getTextSize(conf_text, font, 0.9, 2)[0]
                        conf_x = max(15, w - conf_size[0] - 20)
                        conf_y = h - 20
                        overlay = frame.copy()
                        cv2.rectangle(overlay, (conf_x - 10, conf_y - conf_size[1] - 10), (conf_x + conf_size[0] + 10, conf_y + 6), (0, 0, 0), -1)
                        frame = cv2.addWeighted(overlay, 0.64, frame, 0.36, 0)
                        cv2.putText(frame, conf_text, (conf_x, conf_y), font, 0.9, accent_color, 2, cv2.LINE_AA)
                    
                    # Re-encode to JPEG
                    _, jpeg_bytes = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    jpeg_bytes = jpeg_bytes.tobytes()
                    overlay_applied = True
            except Exception:
                pass  # Fall through to PIL attempt
            
            # Fallback to PIL/Pillow if OpenCV unavailable
            if not overlay_applied:
                try:
                    from PIL import Image, ImageDraw, ImageFont
                    import io
                    
                    img = Image.open(io.BytesIO(jpeg_bytes))
                    draw = ImageDraw.Draw(img, 'RGBA')
                    
                    w, h = img.size
                    # If the service drew an old label above the face box, cover it
                    if face_box is not None and len(face_box) >= 4:
                        try:
                            bx, by, bw, bh = [int(float(v)) for v in face_box[:4]]
                            label_top = max(0, by - 34)
                            draw.rectangle([(max(0, bx - 4), label_top), (min(w, bx + bw + 4), by)], fill=(0, 0, 0, 255))
                        except Exception:
                            pass
                    # Semi-transparent black top bar
                    draw.rectangle([(0, 0), (w, 72)], fill=(0, 0, 0, 180))
                    
                    try:
                        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
                    except Exception:
                        font = ImageFont.load_default()
                    
                    if len(display_text) > 80:
                        display_text = display_text[:77] + "..."
                    
                    text_color = (240, 220, 0, 255)
                    draw.text((18, 18), display_text, fill=text_color, font=font)
                    
                    if face_box is not None and len(face_box) >= 4:
                        try:
                            bx, by, bw, bh = [int(float(v)) for v in face_box[:4]]
                            corner_len = max(15, min(bw, bh) // 8)
                            corner_color = (0, 255, 0, 255) if state in ("SUCCESS", "RECOGNIZED") else (0, 165, 255, 255) if state in ("REQUIRE_2FA", "LIVENESS_CHALLENGE") else (0, 255, 255, 255)
                            draw.rectangle([(bx, by), (bx + bw, by + bh)], outline=corner_color, width=2)
                            draw.line([(bx, by), (bx + corner_len, by)], fill=corner_color, width=2)
                            draw.line([(bx, by), (bx, by + corner_len)], fill=corner_color, width=2)
                            draw.line([(bx + bw, by), (bx + bw - corner_len, by)], fill=corner_color, width=2)
                            draw.line([(bx + bw, by), (bx + bw, by + corner_len)], fill=corner_color, width=2)
                            draw.line([(bx, by + bh), (bx + corner_len, by + bh)], fill=corner_color, width=2)
                            draw.line([(bx, by + bh), (bx, by + bh - corner_len)], fill=corner_color, width=2)
                            draw.line([(bx + bw, by + bh), (bx + bw - corner_len, by + bh)], fill=corner_color, width=2)
                            draw.line([(bx + bw, by + bh), (bx + bw, by + bh - corner_len)], fill=corner_color, width=2)
                        except Exception:
                            pass
                    
                    dist = info.get('dist')
                    if dist is not None and isinstance(dist, (int, float)):
                        confidence = max(0.0, min(100.0, (1.0 - max(0.0, min(1.0, float(dist)))) * 100.0))
                        conf_text = f"{confidence:.1f}%"
                        conf_size = draw.textsize(conf_text, font=font)
                        conf_x = max(15, w - conf_size[0] - 20)
                        conf_y = h - conf_size[1] - 20
                        draw.rectangle([(conf_x - 10, conf_y - 10), (conf_x + conf_size[0] + 10, conf_y + conf_size[1] + 10)], fill=(0, 0, 0, 150))
                        draw.text((conf_x, conf_y), conf_text, fill=(0, 255, 0, 255), font=font)
                    
                    buf_io = io.BytesIO()
                    img.save(buf_io, format='JPEG', quality=70)
                    jpeg_bytes = buf_io.getvalue()
                    overlay_applied = True
                    
                    print(f"[preview] PIL overlay: {display_text}", file=sys.stderr)
                except Exception:
                    pass  # Both CV2 and PIL failed, continue with unchanged frame
            
            if overlay_applied:
                print(f"[preview] Guidance overlay: {display_text[:40]}", file=sys.stderr)
        
        buf = Gst.Buffer.new_allocate(None, len(jpeg_bytes), None)
        buf.fill(0, jpeg_bytes)
        dur       = Gst.SECOND // 15   # ~66ms at 15fps
        buf.pts      = self._pts
        buf.dts      = self._pts
        buf.duration = dur
        self._pts += dur
        ret = self.appsrc.emit("push-buffer", buf)
        if ret != Gst.FlowReturn.OK:
            print(f"[preview] push-buffer: {ret}", file=sys.stderr)

    # IPC fetch thread -----------------------------------------------------------

    def _fetch_loop(self):
        rpc = "process_auth_frame" if self.mode == "auth" else "process_enroll_frame"
        print(f"[preview] Fetch loop started, rpc={rpc}", file=sys.stderr)

        while self._running:
            res = self.ipc.call(rpc, timeout=15)

            if not res.get("success", True):
                print("[preview] Session ended by daemon.", file=sys.stderr)
                break

            b64 = res.get("frame", "")
            if b64:
                try:
                    state = res.get("state", "")
                    msg   = res.get("message", "")
                    self._push_jpeg(
                        base64.b64decode(b64),
                        state=state,
                        message=msg,
                        info=res.get('info', {}),
                        face_box=res.get('face_box'))
                    self._frame_n += 1
                    if self._frame_n % 45 == 0:
                        print(f"[preview] frame={self._frame_n} state={state} msg={msg}",
                              file=sys.stderr)
                except Exception as e:
                    print(f"[preview] decode error: {e}", file=sys.stderr)
            else:
                time.sleep(0.04)

        if self.appsrc:
            self.appsrc.emit("end-of-stream")

    # Lifecycle ------------------------------------------------------------------

    def run(self):
        Gst.init(None)

        self.ipc = SentinelIPCClient(self.socket_path)
        if not self.ipc.connect():
            print(f"[preview] Cannot connect to daemon socket: {self.socket_path}",
                  file=sys.stderr)
            sys.exit(1)
        print(f"[preview] IPC connected → {self.socket_path}", file=sys.stderr)

        self._build_pipeline()

        signal.signal(signal.SIGTERM, lambda s, f: self._quit())
        signal.signal(signal.SIGINT,  lambda s, f: self._quit())

        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            print("[preview] FATAL: Could not start GStreamer pipeline.", file=sys.stderr)
            bus = self.pipeline.get_bus()
            msg = bus.timed_pop_filtered(2 * Gst.SECOND, Gst.MessageType.ERROR)
            if msg:
                err, dbg = msg.parse_error()
                print(f"[preview] Pipeline error: {err} | {dbg}", file=sys.stderr)
            sys.exit(1)

        print(f"[preview] Pipeline PLAYING (mode={self.mode})", file=sys.stderr)
        threading.Thread(target=self._fetch_loop, daemon=True).start()

        self.loop = GLib.MainLoop()
        try:
            self.loop.run()
        except KeyboardInterrupt:
            pass

        self._cleanup()

    def _quit(self):
        self._running = False
        try:
            self.appsrc.emit("end-of-stream")
        except Exception:
            pass
        if self.loop and self.loop.is_running():
            self.loop.quit()

    def _cleanup(self):
        print("[preview] Shutting down pipeline...", file=sys.stderr)
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
        if self.ipc:
            self.ipc.disconnect()
        print("[preview] Done.", file=sys.stderr)


# ── OpenCV fallback (if GStreamer unavailable) ────────────────────────────────

def _opencv_fallback(mode: str, socket_path: str):
    """Used only when gi.repository.Gst is not importable (edge case)."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        print("[preview] Neither GStreamer nor OpenCV available. No preview possible.",
              file=sys.stderr)
        sys.exit(1)

    rpc   = "process_auth_frame" if mode == "auth" else "process_enroll_frame"
    title = f"Sentinel — {'Authentication' if mode == 'auth' else 'Enrollment'}"
    ipc   = SentinelIPCClient(socket_path)
    if not ipc.connect():
        sys.exit(1)

    cv2.namedWindow(title, cv2.WINDOW_AUTOSIZE)
    signal.signal(signal.SIGTERM, lambda s, f: (cv2.destroyAllWindows(), sys.exit(0)))

    while True:
        try:
            if cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) < 1:
                break
        except cv2.error:
            break
        if cv2.waitKey(1) & 0xFF in (ord('q'), 27):
            break

        res = ipc.call(rpc, timeout=15)
        if not res.get("success"):
            break
        b64 = res.get("frame", "")
        if b64:
            try:
                arr = np.frombuffer(base64.b64decode(b64), dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is not None:
                    cv2.imshow(title, frame)
            except Exception:
                pass

    ipc.disconnect()
    cv2.destroyAllWindows()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sentinel Frame Preview (GStreamer)")
    parser.add_argument("--mode",   choices=["auth", "enroll"], default="auth")
    parser.add_argument("--socket", default=DEFAULT_SOCKET_PATH)
    args = parser.parse_args()

    print(f"[preview] mode={args.mode}  socket={args.socket}", file=sys.stderr)
    print(f"[preview] Python={sys.executable} {sys.version.split()[0]}", file=sys.stderr)
    print(f"[preview] GStreamer={HAS_GST}", file=sys.stderr)

    if HAS_GST:
        GStreamerPreview(mode=args.mode, socket_path=args.socket).run()
    else:
        print("[preview] Falling back to OpenCV window.", file=sys.stderr)
        _opencv_fallback(mode=args.mode, socket_path=args.socket)


if __name__ == "__main__":
    main()
