"""
config_manager.py — Config schema, validation, and serialization.
"""

from __future__ import annotations

import logging
from typing import Any

from sentinel_tui.constants import EXPECTED_CONFIG_VERSION

logger = logging.getLogger(__name__)


class ConfigSchema:
    """Validation boundaries for Sentinel config fields."""

    # Defaults and validation ranges
    RANGES = {
        "camera_device_id": {"type": int, "min": 0, "max": 10},
        "camera_width": {"type": int, "min": 320, "max": 3840},
        "camera_height": {"type": int, "min": 240, "max": 2160},
        "camera_fps": {"type": int, "min": 5, "max": 60},
        "min_face_size": {"type": int, "min": 50, "max": 500},
        "spoof_threshold": {"type": float, "min": 0.0, "max": 1.0},
        "challenge_timeout": {"type": float, "min": 5.0, "max": 60.0},
    }

    @staticmethod
    def validate(field: str, value_str: str) -> tuple[bool, str, Any]:
        """
        Validates string input against rules.
        Returns: (is_valid, error_message, typed_value_for_rpc)
        """
        if field not in ConfigSchema.RANGES:
            return True, "", value_str

        rules = ConfigSchema.RANGES[field]
        val_type = rules["type"]

        try:
            # Type cast
            if val_type == int:
                val = int(value_str)
            elif val_type == float:
                val = float(value_str)
            else:
                val = str(value_str)
        except ValueError:
            return False, f"Must be a valid {val_type.__name__}", None

        # Range checks
        if "min" in rules and val < rules["min"]:
            return False, f"Minimum value is {rules['min']}", None
        if "max" in rules and val > rules["max"]:
            return False, f"Maximum value is {rules['max']}", None

        return True, "", val

    @staticmethod
    def to_rpc_format(form_data: dict[str, str]) -> dict[str, Any]:
        """Convert string form data into correct types for RPC update_config payload."""
        output = {"config_version": EXPECTED_CONFIG_VERSION}
        for field, str_val in form_data.items():
            valid, err, typed_val = ConfigSchema.validate(field, str_val)
            if valid and typed_val is not None:
                output[field] = typed_val
            else:
                # If invalid, fallback to string, daemon will complain or convert on its own
                output[field] = str_val
        return output

    @staticmethod
    def check_version(daemon_version: int) -> tuple[bool, str]:
        """Check if daemon's config_version matches expectations."""
        if daemon_version < EXPECTED_CONFIG_VERSION:
            return False, f"Config older than v{EXPECTED_CONFIG_VERSION}. Saving will upgrade it automatically."
        elif daemon_version > EXPECTED_CONFIG_VERSION:
            return False, f"Config format v{daemon_version} is newer than client format v{EXPECTED_CONFIG_VERSION}. Downgrading may cause issues."
        return True, "Config format is up to date."
