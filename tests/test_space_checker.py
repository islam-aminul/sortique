"""Tests for sortique.service.space_checker."""

from __future__ import annotations

import math
from unittest.mock import patch

import pytest

from sortique.constants import SPACE_BUFFER_FACTOR, SPACE_OVERHEAD_FACTOR
from sortique.service.space_checker import SpaceCheckResult, SpaceChecker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _required(total: int) -> int:
    """Mirror the production calculation so tests stay in sync."""
    return math.ceil(total * SPACE_OVERHEAD_FACTOR * SPACE_BUFFER_FACTOR)


# ===================================================================
# Pass scenarios
# ===================================================================


class TestSpaceCheckPass:
    """Destination has enough free space."""

    def test_basic_pass(self):
        total = 1_000_000  # 1 MB source
        required = _required(total)
        fake_free = required + 500_000  # plenty of headroom

        with patch(
            "sortique.service.space_checker.FileSystemHelper.get_free_space",
            return_value=fake_free,
        ):
            result = SpaceChecker().check(total, "/dst")

        assert result.passes is True
        assert result.required_bytes == required
        assert result.available_bytes == fake_free
        assert result.shortfall_bytes == 0

    def test_exact_boundary_passes(self):
        """Available == required should still pass."""
        total = 500_000
        required = _required(total)

        with patch(
            "sortique.service.space_checker.FileSystemHelper.get_free_space",
            return_value=required,
        ):
            result = SpaceChecker().check(total, "/dst")

        assert result.passes is True
        assert result.shortfall_bytes == 0

    def test_zero_source_bytes_passes(self):
        """No files → zero required → always passes."""
        with patch(
            "sortique.service.space_checker.FileSystemHelper.get_free_space",
            return_value=100,
        ):
            result = SpaceChecker().check(0, "/dst")

        assert result.passes is True
        assert result.required_bytes == 0
        assert result.shortfall_bytes == 0

    def test_large_source_passes(self):
        total = 50 * 1024 * 1024 * 1024  # 50 GB
        required = _required(total)
        fake_free = required + 1

        with patch(
            "sortique.service.space_checker.FileSystemHelper.get_free_space",
            return_value=fake_free,
        ):
            result = SpaceChecker().check(total, "/dst")

        assert result.passes is True


# ===================================================================
# Fail scenarios
# ===================================================================


class TestSpaceCheckFail:
    """Destination does not have enough free space."""

    def test_basic_fail_with_correct_shortfall(self):
        total = 1_000_000
        required = _required(total)
        fake_free = 1_000_000  # less than required (~1.43 MB)
        expected_shortfall = required - fake_free

        with patch(
            "sortique.service.space_checker.FileSystemHelper.get_free_space",
            return_value=fake_free,
        ):
            result = SpaceChecker().check(total, "/dst")

        assert result.passes is False
        assert result.required_bytes == required
        assert result.available_bytes == fake_free
        assert result.shortfall_bytes == expected_shortfall
        assert result.shortfall_bytes > 0

    def test_one_byte_short_fails(self):
        total = 1_000_000
        required = _required(total)
        fake_free = required - 1

        with patch(
            "sortique.service.space_checker.FileSystemHelper.get_free_space",
            return_value=fake_free,
        ):
            result = SpaceChecker().check(total, "/dst")

        assert result.passes is False
        assert result.shortfall_bytes == 1

    def test_zero_free_space(self):
        total = 5_000_000
        required = _required(total)

        with patch(
            "sortique.service.space_checker.FileSystemHelper.get_free_space",
            return_value=0,
        ):
            result = SpaceChecker().check(total, "/dst")

        assert result.passes is False
        assert result.shortfall_bytes == required


# ===================================================================
# Multiplier correctness
# ===================================================================


class TestMultiplier:
    """Verify that the overhead + buffer factors are applied correctly."""

    def test_multiplier_value(self):
        """1.3 * 1.1 = 1.43 (approx) applied to source bytes."""
        total = 10_000
        # With math.ceil, required = ceil(10_000 * 1.3 * 1.1) = ceil(14300.0…)
        required = _required(total)

        with patch(
            "sortique.service.space_checker.FileSystemHelper.get_free_space",
            return_value=required,
        ):
            result = SpaceChecker().check(total, "/dst")

        assert result.required_bytes == required
        # The multiplier should be close to 1.43
        assert 1.42 <= result.required_bytes / total <= 1.44

    def test_result_dataclass_fields(self):
        """SpaceCheckResult exposes exactly the documented fields."""
        r = SpaceCheckResult(
            required_bytes=100,
            available_bytes=200,
            passes=True,
            shortfall_bytes=0,
        )
        assert r.required_bytes == 100
        assert r.available_bytes == 200
        assert r.passes is True
        assert r.shortfall_bytes == 0
