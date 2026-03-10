"""Tests for sortique.data.config_manager.ConfigManager."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

import pytest

from sortique.constants import MAX_THREADS
from sortique.data.config_manager import ConfigManager


@pytest.fixture()
def tmp_config_dir(tmp_path: Path) -> Path:
    """Provide a fresh temporary directory for each test."""
    d = tmp_path / "sortique_cfg"
    d.mkdir()
    return d


# ------------------------------------------------------------------
# Default loading
# ------------------------------------------------------------------

class TestDefaultLoading:
    def test_loads_builtin_defaults(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        assert cm.jpeg_quality == 85
        assert cm.threads == 4
        assert cm.verify_copies is False
        assert cm.follow_symlinks is False
        assert cm.musicbrainz_enabled is False

    def test_max_resolution_tuple(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        assert cm.max_resolution == (3840, 2160)

    def test_screenshot_resolutions_is_list(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        resolutions = cm.screenshot_resolutions
        assert isinstance(resolutions, list)
        assert len(resolutions) >= 15
        assert resolutions[0] == [750, 1334]

    def test_editor_patterns_compiled(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        patterns = cm.editor_patterns
        assert len(patterns) > 0
        assert all(isinstance(p, re.Pattern) for p in patterns)
        assert patterns[0].search("Adobe Photoshop CC 2024")

    def test_date_regex_patterns_compiled(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        patterns = cm.date_regex_patterns
        assert len(patterns) == 4
        # First pattern should match "2024-06-15 12:30:45"
        m = patterns[0].search("IMG_2024-06-15_12.30.45.jpg")
        assert m is not None
        assert m.group("Y") == "2024"

    def test_sidecar_extensions(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        assert ".xmp" in cm.sidecar_extensions
        assert ".aae" in cm.sidecar_extensions

    def test_get_missing_key_returns_default(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        assert cm.get("nonexistent_key") is None
        assert cm.get("nonexistent_key", 42) == 42

    def test_get_all_returns_merged_dict(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        merged = cm.get_all()
        assert isinstance(merged, dict)
        assert "jpeg_quality" in merged
        assert "threads" in merged
        assert "date_regex_patterns" in merged


# ------------------------------------------------------------------
# User config override
# ------------------------------------------------------------------

class TestUserConfigOverride:
    def test_user_config_overrides_defaults(self, tmp_config_dir: Path) -> None:
        user_cfg = {"jpeg_quality": 95, "threads": 8}
        (tmp_config_dir / "config.json").write_text(json.dumps(user_cfg))

        cm = ConfigManager(config_dir=str(tmp_config_dir))
        assert cm.jpeg_quality == 95
        assert cm.threads == 8
        # Non-overridden values still come from defaults.
        assert cm.verify_copies is False

    def test_user_config_missing_file(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        assert cm.load_user_config() == {}

    def test_social_media_patterns_overridden(self, tmp_config_dir: Path) -> None:
        user_cfg = {"social_media_image_patterns": ["CUSTOM_*"]}
        (tmp_config_dir / "config.json").write_text(json.dumps(user_cfg))

        cm = ConfigManager(config_dir=str(tmp_config_dir))
        assert cm.social_media_image_patterns == ["CUSTOM_*"]
        # Video patterns still default.
        assert "VID-*-WA*" in cm.social_media_video_patterns


# ------------------------------------------------------------------
# Runtime override priority
# ------------------------------------------------------------------

class TestRuntimeOverride:
    def test_runtime_override_beats_user_config(self, tmp_config_dir: Path) -> None:
        user_cfg = {"jpeg_quality": 70}
        (tmp_config_dir / "config.json").write_text(json.dumps(user_cfg))

        cm = ConfigManager(config_dir=str(tmp_config_dir))
        assert cm.jpeg_quality == 70

        cm.set("jpeg_quality", 50)
        assert cm.jpeg_quality == 50

    def test_runtime_override_beats_defaults(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        assert cm.threads == 4

        cm.set("threads", 8)
        assert cm.threads == 8

    def test_runtime_override_appears_in_get_all(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        cm.set("threads", 12)
        merged = cm.get_all()
        assert merged["threads"] == 12

    def test_runtime_override_not_persisted(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        cm.set("threads", 12)

        # Reload fresh — override should be gone.
        cm2 = ConfigManager(config_dir=str(tmp_config_dir))
        assert cm2.threads == 4


# ------------------------------------------------------------------
# Persistence (save and reload)
# ------------------------------------------------------------------

class TestPersistence:
    def test_save_and_reload(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        cm.save_user_config({"jpeg_quality": 60, "threads": 2})

        cm2 = ConfigManager(config_dir=str(tmp_config_dir))
        assert cm2.jpeg_quality == 60
        assert cm2.threads == 2

    def test_save_strips_default_values(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        # Save a value that matches the default — should NOT appear in file.
        cm.save_user_config({"jpeg_quality": 85})

        raw = json.loads((tmp_config_dir / "config.json").read_text())
        assert "jpeg_quality" not in raw

    def test_save_merges_with_existing(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        cm.save_user_config({"jpeg_quality": 60})
        cm.save_user_config({"threads": 2})

        cm2 = ConfigManager(config_dir=str(tmp_config_dir))
        assert cm2.jpeg_quality == 60
        assert cm2.threads == 2

    def test_creates_config_dir_if_missing(self, tmp_path: Path) -> None:
        new_dir = tmp_path / "nested" / "config"
        assert not new_dir.exists()
        cm = ConfigManager(config_dir=str(new_dir))
        assert new_dir.exists()
        assert cm.jpeg_quality == 85


# ------------------------------------------------------------------
# Validation errors
# ------------------------------------------------------------------

class TestValidation:
    def test_threads_too_low(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        with pytest.raises(ValueError, match="threads"):
            cm.set("threads", 0)

    def test_threads_too_high(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        with pytest.raises(ValueError, match="threads"):
            cm.set("threads", MAX_THREADS + 1)

    def test_threads_not_int(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        with pytest.raises(ValueError, match="threads"):
            cm.set("threads", 2.5)

    def test_jpeg_quality_too_low(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        with pytest.raises(ValueError, match="jpeg_quality"):
            cm.set("jpeg_quality", 0)

    def test_jpeg_quality_too_high(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        with pytest.raises(ValueError, match="jpeg_quality"):
            cm.set("jpeg_quality", 101)

    def test_max_resolution_bad_type(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        with pytest.raises(ValueError, match="max_resolution"):
            cm.set("max_resolution", "1920x1080")

    def test_max_resolution_negative(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        with pytest.raises(ValueError, match="max_resolution"):
            cm.set("max_resolution", [-1, 1080])

    def test_max_resolution_wrong_length(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        with pytest.raises(ValueError, match="max_resolution"):
            cm.set("max_resolution", [1920])

    def test_save_validates_too(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        with pytest.raises(ValueError, match="threads"):
            cm.save_user_config({"threads": 0})


# ------------------------------------------------------------------
# Snapshot immutability
# ------------------------------------------------------------------

class TestSnapshot:
    def test_snapshot_is_deep_copy(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        snap = cm.snapshot()

        # Mutating the snapshot must not affect the manager.
        snap["jpeg_quality"] = 999
        assert cm.jpeg_quality == 85

    def test_snapshot_reflects_overrides(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        cm.set("threads", 8)
        snap = cm.snapshot()
        assert snap["threads"] == 8

    def test_snapshot_not_affected_by_later_changes(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        snap = cm.snapshot()

        cm.set("threads", 12)
        assert snap["threads"] == 4  # Snapshot was taken before the override.

    def test_snapshot_nested_mutation_safe(self, tmp_config_dir: Path) -> None:
        cm = ConfigManager(config_dir=str(tmp_config_dir))
        snap = cm.snapshot()

        # Mutate a nested list in the snapshot.
        snap["screenshot_resolutions"].clear()
        assert len(cm.screenshot_resolutions) >= 15
