"""Tests for build_timemachine_db argument parsing."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.build_timemachine_db import _parse_args, _resolve_paths


class TestParseArgs:
    def test_default_mode_is_full(self):
        args = _parse_args([])
        assert args.mode == "full"

    def test_incremental_mode(self):
        args = _parse_args(["incremental"])
        assert args.mode == "incremental"

    def test_verify_mode(self):
        args = _parse_args(["verify"])
        assert args.mode == "verify"

    def test_csv_flag(self, tmp_path):
        csv = tmp_path / "test.csv"
        csv.touch()
        args = _parse_args(["--csv", str(csv)])
        assert args.csv == csv

    def test_no_validate_flag(self):
        args = _parse_args(["--no-validate"])
        assert args.no_validate is True

    def test_no_validate_default_false(self):
        args = _parse_args([])
        assert args.no_validate is False

    def test_data_dir_override(self, tmp_path):
        args = _parse_args(["--data-dir", str(tmp_path)])
        assert args.data_dir == tmp_path

    def test_config_override(self, tmp_path):
        cfg = tmp_path / "config.json"
        args = _parse_args(["--config", str(cfg)])
        assert args.config == cfg

    def test_downloads_override(self, tmp_path):
        args = _parse_args(["--downloads", str(tmp_path)])
        assert args.downloads == tmp_path

    def test_mode_with_flags(self):
        args = _parse_args(["incremental", "--no-validate"])
        assert args.mode == "incremental"
        assert args.no_validate is True

    def test_invalid_mode_exits(self):
        with pytest.raises(SystemExit):
            _parse_args(["bogus"])


class TestResolvePaths:
    def test_default_paths(self):
        args = _parse_args([])
        paths = _resolve_paths(args)
        assert paths.db_path.name == "timemachine.db"
        assert paths.data_dir.name == "data"

    def test_data_dir_override(self, tmp_path):
        args = _parse_args(["--data-dir", str(tmp_path)])
        paths = _resolve_paths(args)
        assert paths.data_dir == tmp_path
        assert paths.db_path == tmp_path / "timemachine.db"

    def test_config_override(self, tmp_path):
        cfg = tmp_path / "my_config.json"
        args = _parse_args(["--config", str(cfg)])
        paths = _resolve_paths(args)
        assert paths.config == cfg

    def test_downloads_override(self, tmp_path):
        args = _parse_args(["--downloads", str(tmp_path)])
        paths = _resolve_paths(args)
        assert paths.downloads == tmp_path
        assert paths.robinhood_csv == tmp_path / "Robinhood_history.csv"

    def test_csv_passthrough(self, tmp_path):
        csv = tmp_path / "test.csv"
        args = _parse_args(["--csv", str(csv)])
        paths = _resolve_paths(args)
        assert paths.csv == csv

    def test_csv_default_none(self):
        args = _parse_args([])
        paths = _resolve_paths(args)
        assert paths.csv is None

    def test_env_var_fallback(self, tmp_path):
        with patch.dict("os.environ", {"PORTAL_DATA_DIR": str(tmp_path)}):
            args = _parse_args([])
            paths = _resolve_paths(args)
            assert paths.data_dir == tmp_path
