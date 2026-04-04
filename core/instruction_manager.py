import os
import time
import subprocess
import threading
from enum import Enum
import logging

class InstructionType(Enum):
    LOOK_AT_CAMERA = "LOOK_AT_CAMERA"
    FACE_DETECTED = "FACE_DETECTED"
    TURN_LEFT = "TURN_LEFT"
    TURN_RIGHT = "TURN_RIGHT"
    TURN_UP = "TURN_UP"
    TURN_DOWN = "TURN_DOWN"
    BLINK = "BLINK"
    HOLD_STILL = "HOLD_STILL"
    AUTH_SUCCESS_TIER1 = "AUTH_SUCCESS_TIER1"
    AUTH_SUCCESS_TIER2 = "AUTH_SUCCESS_TIER2"
    AUTH_REQUIRE_2FA = "AUTH_REQUIRE_2FA"
    AUTH_FAILED = "AUTH_FAILED"

class InstructionManager:
    """
    Capability-based manager for user guidance during authentication.
    Handles Wayland preview detection, audio caching, and logging.
    """
    
    _cached_alsa_device = None
    
    # Exact mapping based on user specifications
    INSTRUCTION_MAP = {
        InstructionType.TURN_RIGHT: {
            "text": "Please turn your face towards right.",
            "audio": "face_right.wav"
        },
        InstructionType.TURN_LEFT: {
            "text": "Please turn your face towards left.",
            "audio": "face_left.wav"
        },
        InstructionType.TURN_UP: {
            "text": "Please turn your face towards up.",
            "audio": "face_up.wav"
        },
        InstructionType.TURN_DOWN: {
            "text": "Please turn your face towards down.",
            "audio": "face_down.wav"
        },
        InstructionType.BLINK: {
            "text": "Please blink once",
            "audio": "blink.wav"
        },
        InstructionType.AUTH_SUCCESS_TIER1: {
            "text": "System authenicated with the tier 1 authntication",
            "audio": "tier1.wav"
        },
        InstructionType.AUTH_SUCCESS_TIER2: {
            "text": "System authenicated with the tier 2 authntication",
            "audio": "tier2.wav"
        },
        InstructionType.AUTH_REQUIRE_2FA: {
            "text": "Two Factor Authentication required, please enter the password.",
            "audio": "tier3.wav"
        },
        InstructionType.AUTH_FAILED: {
            "text": "Face Recognition Failed, defaulting to password.",
            "audio": "tier4.wav"
        },
        # General instructions without specific custom audio mapped yet, falling back to basic tones or silent
        InstructionType.LOOK_AT_CAMERA: {
            "text": "Looking for face...",
            "audio": "tier1.wav"
        },
        InstructionType.FACE_DETECTED: {
            "text": "Face detected.",
            "audio": "tier2.wav"
        },
        InstructionType.HOLD_STILL: {
            "text": "Hold still...",
            "audio": None
        }
    }

    def __init__(self, audio_dir="/usr/lib/project-sentinel/audio/en"):
        self.logger = logging.getLogger("InstructionManager")
        self.audio_dir = audio_dir
        self.config = {
            "preview_enabled": True,
            "audio_enabled": True,
            "text_enabled": True
        }
        
        # Capability states (detected per session)
        self._can_play_audio = None
        self.audio_cache = {}
        # PAM/bridge attributes
        self.target_user = None
        self.on_text_generated = None

        # Repeater control for persistent instructions (e.g., "Looking for face...")
        self._repeat_lock = threading.Lock()
        self._repeat_event: threading.Event | None = None
        self._repeat_thread: threading.Thread | None = None

        # Preload the audio files
        self._preload_audio()

    def update_config(self, preview_enabled=True, audio_enabled=True, text_enabled=True):
        self.config["preview_enabled"] = preview_enabled
        self.config["audio_enabled"] = audio_enabled
        self.config["text_enabled"] = text_enabled
        # Preserve existing bridge hooks unless explicitly overwritten by callers.
        # Callers that want to change target_user or the on_text_generated hook
        # should set those attributes directly on the instance.

    def _preload_audio(self):
        """Validates that audio files exist so we know what can be played."""
        self.logger.info(f"Preloading audio cache from {self.audio_dir}")
        if not os.path.exists(self.audio_dir):
            self.logger.warning(f"Audio directory does not exist: {self.audio_dir}")
            return
            
        for inst_type, meta in self.INSTRUCTION_MAP.items():
            if meta.get("audio"):
                audio_path = os.path.join(self.audio_dir, meta["audio"])
                if os.path.exists(audio_path):
                    self.audio_cache[inst_type] = audio_path
                else:
                    self.logger.warning(f"Missing audio asset: {audio_path}")

    def can_play_audio(self):
        """
        Capability check for audio playback.
        In modern Linux setups (PipeWire/Wayland), root cannot access user audio devices directly.
        We assume True here since we'll bridge into the user's session context during playback.
        """
        if self._can_play_audio is not None:
            return self._can_play_audio
            
        if not self.config.get("audio_enabled", True):
            self._can_play_audio = False
            return False

        # We assume the user's desktop session can play audio via paplay/aplay
        self._can_play_audio = True
        return True

    def can_show_preview(self, uid=None, xdg_runtime=None, wayland_display=None):
        """
        Strict capability check for Wayland preview window.
        Must receive explicit user session targets.
        """
        if not self.config["preview_enabled"]:
            return False
            
        if not xdg_runtime or not wayland_display:
            return False
            
        # Target the specific Wayland socket
        socket_path = os.path.join(xdg_runtime, wayland_display)
        
        if os.path.exists(socket_path):
            # The socket exists. It implies we *can* attempt to spawn on it
            # assuming we spawn as the correct UID.
            return True
            
        return False

    def _get_alsa_device(self):
        """Auto-discovers the ALSA hardware device with a timeout and caching."""
        if InstructionManager._cached_alsa_device:
            return InstructionManager._cached_alsa_device
            
        try:
            # Use a strict 1-second timeout to prevent deadlocks
            res = subprocess.check_output(["aplay", "-l"], text=True, stderr=subprocess.DEVNULL, timeout=1.0)
            for line in res.splitlines():
                if "card" in line.lower() and "device" in line.lower():
                    # card 0: PCH [HDA Intel PCH], device 0: ALC294 Analog [ALC294 Analog]
                    parts = line.split(":")
                    if len(parts) >= 3:
                        card = parts[0].split()[1]
                        device = parts[1].split(",")[1].split()[1]
                        InstructionManager._cached_alsa_device = f"plughw:{card},{device}"
                        return InstructionManager._cached_alsa_device
        except Exception:
            pass
            
        InstructionManager._cached_alsa_device = "plughw:0,0"
        return InstructionManager._cached_alsa_device

    def _play_audio_async(self, audio_path):
        """Fires off asynchronous audio playback using the best strategy for the environment."""
        target_user = getattr(self, 'target_user', None)
        
        def play():
            try:
                # If we know the user is active, bridge audio via their PulseAudio session
                if target_user:
                    import pwd
                    try:
                        uid = pwd.getpwnam(target_user).pw_uid
                        cmd = [
                            "runuser", "-u", target_user, "--",
                            "/usr/bin/env", f"XDG_RUNTIME_DIR=/run/user/{uid}",
                            "paplay", audio_path
                        ]
                        res = subprocess.run(cmd, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
                        if res.returncode == 0:
                            return # Success via PulseAudio
                    except KeyError:
                        pass

                # If the user has no Pulse session, or paplay failed, fallback to raw ALSA with a shell timeout to prevent hangs.
                device = self._get_alsa_device()
                cmd = ["timeout", "2s", "aplay", "-D", device, "-q", audio_path]
                subprocess.run(cmd, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
            except Exception as e:
                self.logger.error(f"Failed to play audio {audio_path}: {e}")
                
        t = threading.Thread(target=play, daemon=True)
        t.start()

    def _show_notification_async(self, text_msg):
        """Sends a desktop notification to the target user's Wayland/X11 session."""
        target_user = getattr(self, 'target_user', None)
        def show():
            try:
                if target_user:
                    import pwd
                    try:
                        uid = pwd.getpwnam(target_user).pw_uid
                        # notify-send needs the user's DBus session bus address
                        cmd = [
                            "runuser", "-u", target_user, "--",
                            "/usr/bin/env", f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
                            "notify-send", "-a", "Project Sentinel", "-t", "3000", "Sentinel Authentication", text_msg
                        ]
                        subprocess.run(cmd, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
                    except KeyError:
                        pass
            except Exception as e:
                self.logger.error(f"Failed to show notification: {e}")
                
        t = threading.Thread(target=show, daemon=True)
        t.start()

    def send_instruction(self, instruction_type: InstructionType) -> str:
        """
        Orchestrates the guidance pipeline.
        Returns the text message intended for the PAM output.
        """
        meta = self.INSTRUCTION_MAP.get(instruction_type)
        if not meta:
            self.logger.warning(f"Unknown instruction type: {instruction_type}")
            return ""

        def _do_send():
            text_msg = meta["text"] if self.config.get("text_enabled", True) else ""
            audio_file = meta.get("audio")
            audio_played = False

            # DEBUG: Persistent log to track daemon internal state
            try:
                with open("/tmp/sentinel_audio.log", "a") as f:
                    f.write(f"{time.ctime()} | Instruction: {instruction_type.name} | Text: {text_msg} | User: {self.target_user}\n")
                try:
                    os.chmod("/tmp/sentinel_audio.log", 0o666) # Ensure everyone can read
                except Exception:
                    pass
            except Exception:
                pass

            # Text/Notification Pipeline
            if text_msg:
                self._show_notification_async(text_msg)
                hook = getattr(self, "on_text_generated", None)
                if hook:
                    try:
                        hook(text_msg)
                    except Exception:
                        self.logger.debug("on_text_generated hook raised an exception", exc_info=True)

            # Audio Pipeline
            if audio_file and instruction_type in self.audio_cache and self.can_play_audio():
                audio_path = self.audio_cache[instruction_type]
                self._play_audio_async(audio_path)
                audio_played = True

            # Logging for tracing
            self.logger.info(
                f"Instruction: {instruction_type.name} | "
                f"Audio: {audio_file if audio_played else 'none'} | "
                f"Result: sent"
            )
            return text_msg

        # Cancel any existing repeater
        with self._repeat_lock:
            if self._repeat_event:
                self._repeat_event.set()
            self._repeat_event = threading.Event()
            event = self._repeat_event

        # Send once immediately
        text = _do_send()

        # Determine if we should repeat based on instruction type
        # PERSISTENT instructions (like LOOK_AT_CAMERA) repeat; CHALLENGE instructions send once
        should_repeat = instruction_type in (
            InstructionType.LOOK_AT_CAMERA,
            InstructionType.HOLD_STILL,
        )

        if should_repeat:
            # Start background repeater for persistent instructions (e.g., "Looking for face...")
            def _repeater(evt: threading.Event):
                try:
                    # Repeat up to 3 additional times (total 4) with 2.5s interval
                    for _ in range(3):
                        if evt.wait(2.5):
                            break
                        _do_send()
                except Exception:
                    self.logger.exception("Instruction repeater failed")

            t = threading.Thread(target=_repeater, args=(event,), daemon=True)
            with self._repeat_lock:
                self._repeat_thread = t
            t.start()
        else:
            # Challenge instructions (TURN_LEFT, BLINK, etc) send once only - no repeater
            # This gives immediate feedback on the preview window
            self.logger.debug(f"{instruction_type.name}: Single-send (no repeater, immediate display)")

        return text
