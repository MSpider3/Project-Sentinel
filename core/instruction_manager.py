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
    
    # Exact mapping based on user specifications
    INSTRUCTION_MAP = {
        InstructionType.TURN_RIGHT: {
            "text": "Please turn you face towards right.",
            "audio": "face_rigth.wav"
        },
        InstructionType.TURN_LEFT: {
            "text": "Please turn you face towards left.",
            "audio": "face_left.wav"
        },
        InstructionType.TURN_UP: {
            "text": "Please turn you face towards up.",
            "audio": "face_up.wav"
        },
        InstructionType.TURN_DOWN: {
            "text": "Please turn you face towards down.",
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
            "audio": None
        },
        InstructionType.FACE_DETECTED: {
            "text": "Face detected.",
            "audio": None
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
        
        # Preload the audio files
        self._preload_audio()

    def update_config(self, preview_enabled=True, audio_enabled=True, text_enabled=True):
        self.config["preview_enabled"] = preview_enabled
        self.config["audio_enabled"] = audio_enabled
        self.config["text_enabled"] = text_enabled

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
        Checks if `aplay` is available and has devices.
        Caches the result to avoid spawning subprocesses frequently.
        """
        if self._can_play_audio is not None:
            return self._can_play_audio
            
        if not self.config["audio_enabled"]:
            self._can_play_audio = False
            return False

        try:
            # Check if aplay exists and lists at least one playback device
            result = subprocess.run(["aplay", "-l"], capture_output=True, text=True, timeout=1.0)
            if result.returncode == 0 and "List of PLAYBACK Hardware Devices" in result.stdout:
                self._can_play_audio = True
                return True
        except Exception as e:
            self.logger.debug(f"Audio capability check failed: {e}")
            
        self._can_play_audio = False
        return False

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

    def _play_audio_async(self, audio_path):
        """Fires off asynchronous audio playback via `aplay`."""
        def play():
            try:
                # -q for quiet, don't block
                subprocess.run(["aplay", "-q", audio_path], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
            except Exception as e:
                self.logger.error(f"Failed to play audio {audio_path}: {e}")
                
        t = threading.Thread(target=play, daemon=True)
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

        text_msg = meta["text"] if self.config["text_enabled"] else ""
        audio_file = meta.get("audio")
        audio_played = False
        
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
