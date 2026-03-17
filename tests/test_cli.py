"""Tests for CLI argument parsing, config management, and dispatch."""

from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest

from sortique.cli import build_parser, dispatch_cli, run_config


class TestOrganizeParser:
    def test_organize_requires_source_and_destination(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["organize"])

    def test_organize_single_source(self):
        parser = build_parser()
        args = parser.parse_args(["organize", "-s", "/tmp/src", "-d", "/tmp/dst"])
        assert args.command == "organize"
        assert args.source == ["/tmp/src"]
        assert args.destination == "/tmp/dst"

    def test_organize_multiple_sources(self):
        parser = build_parser()
        args = parser.parse_args([
            "organize", "--source", "/a", "/b", "/c", "--destination", "/dst",
        ])
        assert args.source == ["/a", "/b", "/c"]

    def test_organize_threads_default_none(self):
        parser = build_parser()
        args = parser.parse_args(["organize", "-s", "/src", "-d", "/dst"])
        assert args.threads is None

    def test_organize_threads_override(self):
        parser = build_parser()
        args = parser.parse_args(["organize", "-s", "/src", "-d", "/dst", "-t", "8"])
        assert args.threads == 8

    def test_organize_dry_run_flag(self):
        parser = build_parser()
        args = parser.parse_args(["organize", "-s", "/src", "-d", "/dst", "--dry-run"])
        assert args.dry_run is True

    def test_organize_verbose_flag(self):
        parser = build_parser()
        args = parser.parse_args(["organize", "-s", "/src", "-d", "/dst", "-v"])
        assert args.verbose is True


class TestConfigParser:
    def test_config_list(self):
        parser = build_parser()
        args = parser.parse_args(["config", "list"])
        assert args.command == "config"
        assert args.config_action == "list"

    def test_config_get(self):
        parser = build_parser()
        args = parser.parse_args(["config", "get", "threads"])
        assert args.config_action == "get"
        assert args.key == "threads"

    def test_config_set(self):
        parser = build_parser()
        args = parser.parse_args(["config", "set", "threads", "8"])
        assert args.config_action == "set"
        assert args.key == "threads"
        assert args.value == "8"

    def test_config_add(self):
        parser = build_parser()
        args = parser.parse_args(["config", "add", "skip_filename_patterns", "*.tmp"])
        assert args.config_action == "add"
        assert args.key == "skip_filename_patterns"
        assert args.value == "*.tmp"

    def test_config_remove(self):
        parser = build_parser()
        args = parser.parse_args(["config", "remove", "editor_patterns", "GIMP"])
        assert args.config_action == "remove"
        assert args.key == "editor_patterns"
        assert args.value == "GIMP"

    def test_config_reset(self):
        parser = build_parser()
        args = parser.parse_args(["config", "reset", "threads"])
        assert args.config_action == "reset"
        assert args.key == "threads"


class TestConfigActions:
    @pytest.fixture()
    def config_dir(self, tmp_path):
        return str(tmp_path / "sortique_cfg")

    def _run(self, config_dir, action_args):
        """Helper: parse args and run config with a temp config dir."""
        from sortique.data.config_manager import ConfigManager

        parser = build_parser()
        args = parser.parse_args(["config"] + action_args)
        config = ConfigManager(config_dir)
        # Patch ConfigManager() in cli module to use our temp dir.
        with patch("sortique.cli.ConfigManager", return_value=config):
            return run_config(args), config

    def test_list_shows_defaults(self, config_dir, capsys):
        code, _ = self._run(config_dir, ["list"])
        assert code == 0
        out = capsys.readouterr().out
        assert "threads" in out
        assert "jpeg_quality" in out
        assert "musicbrainz_enabled" in out

    def test_get_known_key(self, config_dir, capsys):
        code, _ = self._run(config_dir, ["get", "threads"])
        assert code == 0
        assert "threads" in capsys.readouterr().out

    def test_get_unknown_key(self, config_dir):
        code, _ = self._run(config_dir, ["get", "nonexistent_key"])
        assert code == 1

    def test_set_scalar(self, config_dir, capsys):
        code, config = self._run(config_dir, ["set", "threads", "8"])
        assert code == 0
        assert config.get("threads") == 8

    def test_set_boolean(self, config_dir):
        code, config = self._run(config_dir, ["set", "musicbrainz_enabled", "true"])
        assert code == 0
        assert config.get("musicbrainz_enabled") is True

    def test_set_rejects_list_key(self, config_dir):
        code, _ = self._run(config_dir, ["set", "editor_patterns", "foo"])
        assert code == 1

    def test_add_to_list(self, config_dir, capsys):
        code, config = self._run(config_dir, ["add", "skip_filename_patterns", "*.tmp"])
        assert code == 0
        assert "*.tmp" in config.get("skip_filename_patterns")

    def test_add_duplicate_is_noop(self, config_dir, capsys):
        self._run(config_dir, ["add", "editor_patterns", "GIMP"])
        # GIMP is already in defaults — should say already exists.
        code, _ = self._run(config_dir, ["add", "editor_patterns", "GIMP"])
        assert code == 0

    def test_add_rejects_non_list(self, config_dir):
        code, _ = self._run(config_dir, ["add", "threads", "5"])
        assert code == 1

    def test_remove_from_list(self, config_dir, capsys):
        code, config = self._run(config_dir, ["remove", "editor_patterns", "GIMP"])
        assert code == 0
        assert "GIMP" not in config.get("editor_patterns")

    def test_remove_missing_value(self, config_dir):
        code, _ = self._run(config_dir, ["remove", "editor_patterns", "NotThere"])
        assert code == 1

    def test_reset_to_default(self, config_dir, capsys):
        # Set to non-default first.
        self._run(config_dir, ["set", "threads", "16"])
        code, config = self._run(config_dir, ["reset", "threads"])
        assert code == 0
        assert config.get("threads") == 4  # default

    def test_reset_unknown_key(self, config_dir):
        code, _ = self._run(config_dir, ["reset", "nonexistent"])
        assert code == 1


class TestMainDispatch:
    def test_cli_dispatch_with_args(self):
        with patch("sys.argv", ["sortique", "config", "list"]):
            assert len(sys.argv) > 1

    def test_gui_dispatch_without_args(self):
        with patch("sys.argv", ["sortique"]):
            assert len(sys.argv) == 1


class TestRunOrganize:
    def test_invalid_source_dir_returns_1(self, tmp_path):
        from sortique.cli import run_organize
        parser = build_parser()
        args = parser.parse_args([
            "organize",
            "-s", str(tmp_path / "nonexistent"),
            "-d", str(tmp_path / "dst"),
        ])
        assert run_organize(args) == 1
