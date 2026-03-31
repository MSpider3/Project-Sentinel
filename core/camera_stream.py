import cv2
import threading
import time
import logging
import os
import stat
import errno as _errno_mod
import subprocess

# Use sentinel_logger if available (production), fall back silently for tests
try:
    import sentinel_logger as _slog
    logger = _slog.get("camera_stream")
except ImportError:
    logger = logging.getLogger(__name__)


def _check_video_devices() -> list[int]:
    """
    Probe /dev/video* nodes and return indices that exist.

    IMPORTANT: Under SELinux (Fedora), a raw os.open() on /dev/video* may fail
    with EACCES even when running as root, because the process context (init_t)
    may not be allowed to open v4l_device_t. However, OpenCV's VideoCapture()
    uses a different internal code path and can often succeed. Therefore:
    - We collect ALL existing video device indices (even if our raw open fails)
    - We log any EACCES as WARNING (not a terminal error)
    - We always let OpenCV try, regardless of probe results
    """
    found = []
    selinux_blocked = []

    # Enumerate all /dev/video* nodes
    devs = sorted(
        [d for d in os.listdir("/dev") if d.startswith("video")],
        key=lambda x: int(x[5:]) if x[5:].isdigit() else 99
    )

    if not devs:
        logger.error("[camera-probe] No /dev/video* devices found at all. Is a camera plugged in?")
        return []

    for dev_name in devs:
        path = f"/dev/{dev_name}"
        idx = int(dev_name[5:]) if dev_name[5:].isdigit() else None
        if idx is None:
            continue

        try:
            st = os.stat(path)
            mode = st.st_mode
            gid = st.st_gid
            uid = st.st_uid
            perm_str = stat.filemode(mode)
            logger.info(f"[camera-probe] {path}: owner={uid}/{gid} perms={perm_str}")
        except Exception as e:
            logger.warning(f"[camera-probe] Could not stat {path}: {e}")
            continue

        # Try a raw open() to get the real errno — for DIAGNOSTIC purposes only.
        # We always add the index to 'found' as long as the device FILE exists,
        # because OpenCV may still succeed where os.open() fails (e.g. SELinux).
        try:
            fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
            os.close(fd)
            logger.info(f"[camera-probe] {path}: raw open OK — index {idx} confirmed usable")
        except OSError as e:
            if e.errno == _errno_mod.EACCES:
                # SELinux may block raw open() but allow OpenCV's VideoCapture.
                # Log as WARNING and still allow OpenCV to attempt this index.
                logger.warning(
                    f"[camera-probe] {path}: raw os.open() denied (EACCES) — "
                    f"likely SELinux (init_t context). Will still attempt via OpenCV. "
                    f"Daemon UID={os.getuid()}, GIDs={os.getgroups()}"
                )
                selinux_blocked.append(idx)
            elif e.errno == _errno_mod.EBUSY:
                logger.warning(f"[camera-probe] {path}: device BUSY — will try via OpenCV anyway")
            elif e.errno == _errno_mod.ENOENT:
                logger.warning(f"[camera-probe] {path}: device node disappeared — skipping")
                continue  # truly gone, don't try
            else:
                logger.warning(f"[camera-probe] {path}: open errno={e.errno} ({e}) — will still try via OpenCV")

        found.append(idx)

    # Check SELinux AVC denials — but only flag ones specifically about v4l_device_t
    try:
        avc = subprocess.run(
            ["ausearch", "-m", "AVC", "-ts", "recent"],
            capture_output=True, text=True, timeout=3
        )
        if avc.returncode == 0 and avc.stdout:
            if "v4l_device_t" in avc.stdout or "v4l" in avc.stdout.lower():
                logger.error(
                    f"[camera-probe] SELinux AVC denials specifically for v4l/video detected!\n"
                    f"Fix: sudo ausearch -m avc -c sentinel-daemon | audit2allow -M sentinel-cam && "
                    f"sudo semodule -i sentinel-cam.pp\n"
                    f"AVC excerpt: {avc.stdout[:400]}"
                )
            else:
                logger.debug("[camera-probe] Recent AVC denials exist but none for v4l — camera not affected by SELinux")
    except Exception:
        logger.debug("[camera-probe] ausearch not available — SELinux check skipped")

    if selinux_blocked:
        logger.warning(
            f"[camera-probe] Indices {selinux_blocked} were blocked by raw open() (SELinux). "
            f"Proceeding with OpenCV — this often works despite the raw probe failing."
        )

    return found


class CameraStream:
    """
    Threaded camera capture to improve performance.
    Always creates a dedicated thread to read frames from the camera,
    so the main application doesn't block while waiting for I/O.
    """
    def __init__(self, src=0, width=640, height=480, fps=15):
        self.src = src
        self.width = width
        self.height = height
        self.fps = fps
        self.stream = None
        self.stopped = False
        self.grabbed = False
        self.frame = None
        self.thread = None
        self.last_frame_time = time.time()
        self.lock = threading.Lock()

    def start(self):
        """Starts the video stream thread."""
        logger.info(f"[camera] Starting camera. Requested index={self.src}, UID={os.getuid()}, GIDs={os.getgroups()}")

        # Run device probe — diagnostic only, does NOT abort even if empty.
        # Under SELinux, raw probe may fail but OpenCV can still open the device.
        usable = _check_video_devices()
        if not usable:
            # No /dev/video* nodes found at all — but still try hardcoded fallbacks.
            logger.warning("[camera] Device probe returned no indices — trying hardcoded fallbacks 0, 1")
            usable = [0, 1]

        # Build candidate list: requested index first, then all probed, then common fallbacks
        candidates = [self.src] + [i for i in usable if i != self.src] + [0, 1]
        seen = set()
        ordered = [x for x in candidates if not (x in seen or seen.add(x))]

        backends = [
            (cv2.CAP_V4L2,  "V4L2"),
            (cv2.CAP_FFMPEG, "FFMPEG"),
            (cv2.CAP_ANY,    "AUTO"),
        ]

        for idx in ordered:
            for backend, bname in backends:
                try:
                    logger.info(f"[camera] Trying index={idx} backend={bname}...")
                    cap = cv2.VideoCapture(idx, backend)
                    
                    # Configure BEFORE reading to prevent V4L2 pipeline breaks
                    try:
                        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                        cap.set(cv2.CAP_PROP_FPS, self.fps)
                        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    except Exception as e:
                        logger.warning(f"[camera] Prop set warning on {bname}: {e}")

                    if cap.isOpened():
                        grabbed, frame = cap.read()
                        if grabbed and frame is not None and getattr(frame, 'shape', None):
                            self.stream = cap
                            self.frame = frame
                            self.grabbed = True
                            self.last_frame_time = time.time()
                            self.src = idx
                            logger.info(f"[camera] SUCCESS: index={idx} backend={bname}")
                            break
                        else:
                            logger.warning(f"[camera] index={idx} backend={bname}: opened but first read() failed")
                            cap.release()
                    else:
                        logger.warning(f"[camera] index={idx} backend={bname}: isOpened() returned False")
                        cap.release()
                except Exception as e:
                    logger.error(f"[camera] index={idx} backend={bname}: exception {type(e).__name__}: {e}")
            else:
                continue
            break  # inner break found a working combo

        if self.stream is None or not self.stream.isOpened():
            logger.error(
                "[camera] FAILED to open camera on any index or backend. "
                "Check: (1) camera is plugged in, (2) su/sudo grants video group, "
                "(3) SELinux allows v4l access, (4) another app isn't holding the device."
            )
            return self

        self.stopped = False
        self.thread = threading.Thread(target=self.update, args=())
        self.thread.daemon = True
        self.thread.start()
        logger.info(f"[camera] Stream thread started. index={self.src} {self.width}x{self.height}@{self.fps}fps")
        return self

    def update(self):
        """Background thread loop to keep reading frames."""
        sleep_time = 0.005
        consecutive_failures = 0

        while True:
            if self.stopped:
                return

            try:
                if self.stream is None or not self.stream.isOpened():
                    self.stopped = True
                    break
                    
                grabbed, frame = self.stream.read()
                with self.lock:
                    self.grabbed = grabbed
                    if grabbed:
                        self.frame = frame
                        self.last_frame_time = time.time()
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                        if consecutive_failures % 30 == 0:
                            logger.warning(f"[camera] {consecutive_failures} consecutive read() failures on index={self.src}")
                        if consecutive_failures > 50:
                            logger.error("[camera] Fatal stream failure limit reached. Auto-stopping camera thread.")
                            self.stopped = True
                time.sleep(sleep_time)
            except Exception as e:
                logger.error(f"[camera] Exception in read thread: {e}")
                time.sleep(0.1)

    def read(self):
        """Return the most recent frame."""
        with self.lock:
            if not self.grabbed:
                return None
            if time.time() - self.last_frame_time > 2.0:
                logger.error(f"[camera] Watchdog timeout: No frames received in 2.0s on index {self.src}")
                self.grabbed = False
                return None
            return self.frame.copy() if self.frame is not None else None

    def stop(self):
        """Stop the thread and release resources."""
        self.stopped = True
        
        # Release stream immediately to unblock any pending read()
        with self.lock:
            if self.stream:
                try:
                    self.stream.release()
                except Exception as e:
                    logger.error(f"[camera] Error releasing stream during stop: {e}")
                self.stream = None
                
        if self.thread is not None:
            self.thread.join(timeout=2.0)
            if self.thread.is_alive():
                logger.error("[camera] Thread failed to join! OpenCV read() is deadlocked.")

    def __del__(self):
        self.stop()
