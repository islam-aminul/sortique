"""Tests for sortique.engine.metadata.exiftool_common."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from sortique.engine.metadata.exiftool_common import (
    is_exiftool_available,
    parse_exiftool_date,
    run_exiftool,
)


# ===========================================================================
# 1. is_exiftool_available
# ===========================================================================

class TestIsExiftoolAvailable:

    def setup_method(self):
        is_exiftool_available.cache_clear()

    def test_returns_bool(self):
        result = is_exiftool_available()
        assert isinstance(result, bool)

    @patch("sortique.engine.metadata.exiftool_common.shutil.which", return_value=None)
    def test_not_available(self, mock_which):
        is_exiftool_available.cache_clear()
        assert is_exiftool_available() is False

    @patch("sortique.engine.metadata.exiftool_common.shutil.which", return_value="/usr/bin/exiftool")
    def test_available(self, mock_which):
        is_exiftool_available.cache_clear()
        assert is_exiftool_available() is True

    @patch("sortique.engine.metadata.exiftool_common.shutil.which", return_value="/usr/bin/exiftool")
    def test_caches_result(self, mock_which):
        is_exiftool_available.cache_clear()
        is_exiftool_available()
        is_exiftool_available()
        is_exiftool_available()
        mock_which.assert_called_once()


# ===========================================================================
# 2. run_exiftool
# ===========================================================================

class TestRunExiftool:

    @patch("sortique.engine.metadata.exiftool_common.is_exiftool_available", return_value=False)
    def test_returns_none_when_not_available(self, mock_avail):
        assert run_exiftool("test.jpg") is None

    @patch("sortique.engine.metadata.exiftool_common.is_exiftool_available", return_value=True)
    @patch("sortique.engine.metadata.exiftool_common.subprocess.run")
    def test_returns_none_on_nonzero_exit(self, mock_run, mock_avail):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        assert run_exiftool("bad.jpg") is None

    @patch("sortique.engine.metadata.exiftool_common.is_exiftool_available", return_value=True)
    @patch("sortique.engine.metadata.exiftool_common.subprocess.run")
    def test_returns_dict_on_success(self, mock_run, mock_avail):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([{"Make": "Canon", "Model": "EOS R5"}]),
        )
        result = run_exiftool("photo.jpg")
        assert result == {"Make": "Canon", "Model": "EOS R5"}

    @patch("sortique.engine.metadata.exiftool_common.is_exiftool_available", return_value=True)
    @patch("sortique.engine.metadata.exiftool_common.subprocess.run")
    def test_returns_none_on_empty_json(self, mock_run, mock_avail):
        mock_run.return_value = MagicMock(returncode=0, stdout="[]")
        assert run_exiftool("empty.jpg") is None

    @patch("sortique.engine.metadata.exiftool_common.is_exiftool_available", return_value=True)
    @patch("sortique.engine.metadata.exiftool_common.subprocess.run")
    def test_returns_none_on_invalid_json(self, mock_run, mock_avail):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json")
        assert run_exiftool("broken.jpg") is None

    @patch("sortique.engine.metadata.exiftool_common.is_exiftool_available", return_value=True)
    @patch("sortique.engine.metadata.exiftool_common.subprocess.run", side_effect=TimeoutError)
    def test_returns_none_on_timeout(self, mock_run, mock_avail):
        assert run_exiftool("huge.jpg") is None

    @patch("sortique.engine.metadata.exiftool_common.is_exiftool_available", return_value=True)
    @patch("sortique.engine.metadata.exiftool_common.subprocess.run")
    def test_passes_timeout_parameter(self, mock_run, mock_avail):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([{"Make": "Nikon"}]),
        )
        run_exiftool("photo.nef", timeout=60)
        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == 60


# ===========================================================================
# 3. parse_exiftool_date
# ===========================================================================

class TestParseExiftoolDate:

    def test_standard_exif_format(self):
        result = parse_exiftool_date("2024:06:15 14:30:00")
        assert result == datetime(2024, 6, 15, 14, 30, 0)

    def test_missing_seconds(self):
        result = parse_exiftool_date("2024:06:15 14:30")
        assert result == datetime(2024, 6, 15, 14, 30, 0)

    def test_dash_separated(self):
        result = parse_exiftool_date("2024-06-15 14:30:00")
        assert result == datetime(2024, 6, 15, 14, 30, 0)

    def test_iso_with_t(self):
        result = parse_exiftool_date("2024-06-15T14:30:00")
        assert result == datetime(2024, 6, 15, 14, 30, 0)

    def test_iso_with_fractional_seconds(self):
        result = parse_exiftool_date("2024-06-15T14:30:00.500")
        assert result is not None
        assert result.year == 2024

    def test_date_only(self):
        result = parse_exiftool_date("2024:06:15")
        assert result == datetime(2024, 6, 15)

    def test_none_returns_none(self):
        assert parse_exiftool_date(None) is None

    def test_empty_returns_none(self):
        assert parse_exiftool_date("") is None

    def test_whitespace_returns_none(self):
        assert parse_exiftool_date("   ") is None

    def test_all_zeros_returns_none(self):
        assert parse_exiftool_date("0000:00:00 00:00:00") is None

    def test_bytes_input(self):
        result = parse_exiftool_date(b"2024:06:15 14:30:00")
        assert result == datetime(2024, 6, 15, 14, 30, 0)

    def test_strips_timezone_z(self):
        result = parse_exiftool_date("2024-06-15T14:30:00Z")
        assert result == datetime(2024, 6, 15, 14, 30, 0)

    def test_strips_timezone_positive_offset(self):
        result = parse_exiftool_date("2024:06:15 14:30:00+05:30")
        assert result == datetime(2024, 6, 15, 14, 30, 0)

    def test_strips_timezone_negative_offset(self):
        result = parse_exiftool_date("2024:06:15 14:30:00-08:00")
        assert result == datetime(2024, 6, 15, 14, 30, 0)

    def test_unparseable_returns_none(self):
        assert parse_exiftool_date("not a date") is None
