"""Layered configuration manager for Sortique.

Priority (highest wins): runtime overrides > user config file > defaults.json
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

from sortique.constants import MAX_THREADS

# Package-level defaults shipped with the application.
# In a frozen PyInstaller build all data files land in sys._MEIPASS; the
# normal source layout is used otherwise.
if getattr(sys, "frozen", False):
    _DEFAULTS_PATH = Path(sys._MEIPASS) / "config" / "defaults.json"
else:
    _DEFAULTS_PATH = Path(__file__).resolve().parent.parent / "config" / "defaults.json"


class ConfigManager:
    """Three-layer config: built-in defaults -> user JSON file -> runtime overrides."""

    def __init__(self, config_dir: str | None = None) -> None:
        self._config_dir = Path(config_dir) if config_dir else Path.home() / ".sortique"
        self._config_dir.mkdir(parents=True, exist_ok=True)

        self._defaults: dict = self._load_defaults()
        self._user: dict = self.load_user_config()
        self._overrides: dict = {}

        # Regex compilation cache: key -> (source_list, compiled)
        self._pattern_cache: dict[str, tuple[list[str], list[re.Pattern[str]]]] = {}

    # ------------------------------------------------------------------
    # Core access
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Return the effective value for *key* (flat lookup, no dot nesting)."""
        if key in self._overrides:
            return self._overrides[key]
        if key in self._user:
            return self._user[key]
        return self._defaults.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a runtime override (not persisted to disk)."""
        self._validate(key, value)
        self._overrides[key] = value

    def get_all(self) -> dict:
        """Return the fully merged config as a flat dict."""
        merged: dict = {}
        merged.update(self._defaults)
        merged.update(self._user)
        merged.update(self._overrides)
        return merged

    def snapshot(self) -> dict:
        """Return an immutable deep-copy of the current merged config."""
        return copy.deepcopy(self.get_all())

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _load_defaults() -> dict:
        with open(_DEFAULTS_PATH, encoding="utf-8") as f:
            return json.load(f)

    def load_user_config(self) -> dict:
        """Read user config.json, returning empty dict if absent."""
        path = self._config_dir / "config.json"
        if not path.exists():
            return {}
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def save_user_config(self, overrides: dict) -> None:
        """Merge *overrides* into the user config and write to disk.

        Only keys whose values differ from the built-in defaults are stored.
        """
        for key, value in overrides.items():
            self._validate(key, value)

        self._user.update(overrides)

        # Strip entries that match defaults so the file stays minimal.
        to_write = {k: v for k, v in self._user.items() if v != self._defaults.get(k)}

        path = self._config_dir / "config.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(to_write, f, indent=2)

        # Keep in-memory user layer in sync with what we just wrote.
        self._user = to_write

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate(key: str, value: Any) -> None:
        if key == "threads":
            if not isinstance(value, int) or value < 1 or value > MAX_THREADS:
                raise ValueError(
                    f"threads must be an integer between 1 and {MAX_THREADS}, got {value!r}"
                )
        elif key == "jpeg_quality":
            if not isinstance(value, int) or value < 1 or value > 100:
                raise ValueError(
                    f"jpeg_quality must be an integer between 1 and 100, got {value!r}"
                )
        elif key == "max_resolution":
            if (
                not isinstance(value, (list, tuple))
                or len(value) != 2
                or not all(isinstance(v, int) and v > 0 for v in value)
            ):
                raise ValueError(
                    f"max_resolution must be [width, height] with positive integers, got {value!r}"
                )
        elif key == "editor_exclusions":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError(
                    f"editor_exclusions must be a list of strings, got {value!r}"
                )
        elif key == "skip_filename_patterns":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError(
                    f"skip_filename_patterns must be a list of strings, got {value!r}"
                )
        elif key == "call_recording_patterns":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError(
                    f"call_recording_patterns must be a list of strings, got {value!r}"
                )

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def jpeg_quality(self) -> int:
        return self.get("jpeg_quality")

    @property
    def max_resolution(self) -> tuple[int, int]:
        val = self.get("max_resolution")
        return tuple(val)

    @property
    def threads(self) -> int:
        return self.get("threads")

    @property
    def verify_copies(self) -> bool:
        return self.get("verify_copies")

    @property
    def follow_symlinks(self) -> bool:
        return self.get("follow_symlinks")

    @property
    def musicbrainz_enabled(self) -> bool:
        return self.get("musicbrainz_enabled")

    @property
    def screenshot_resolutions(self) -> list[list[int]]:
        return self.get("screenshot_resolutions")

    @property
    def editor_patterns(self) -> list[re.Pattern[str]]:
        return self._compile_patterns(self.get("editor_patterns", []))

    @property
    def editor_exclusions(self) -> list[re.Pattern[str]]:
        return self._compile_patterns(self.get("editor_exclusions", []))

    @property
    def social_media_image_patterns(self) -> list[str]:
        return self.get("social_media_image_patterns", [])

    @property
    def social_media_video_patterns(self) -> list[str]:
        return self.get("social_media_video_patterns", [])

    @property
    def sidecar_extensions(self) -> list[str]:
        return self.get("sidecar_extensions", [])

    @property
    def date_regex_patterns(self) -> list[re.Pattern[str]]:
        return self._compile_patterns(self.get("date_regex_patterns", []))

    @property
    def skip_filename_patterns(self) -> list[str]:
        return self.get("skip_filename_patterns", [])

    @property
    def call_recording_patterns(self) -> list[str]:
        return self.get("call_recording_patterns", [])

    # ------------------------------------------------------------------
    # Regex compilation cache
    # ------------------------------------------------------------------

    def _compile_patterns(self, patterns: list[str]) -> list[re.Pattern[str]]:
        """Return compiled regex objects, using a cache keyed on the source list."""
        cache_key = id(patterns)
        # Also fall back to content-based cache key for lists built on the fly.
        content_key = tuple(patterns)

        for key in (cache_key, content_key):
            cached = self._pattern_cache.get(key)
            if cached is not None and cached[0] == patterns:
                return cached[1]

        compiled = [re.compile(p) for p in patterns]
        self._pattern_cache[content_key] = (list(patterns), compiled)
        return compiled
