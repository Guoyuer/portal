"""Tests for build_timemachine_db argument parsing."""
from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from etl.build import BuildPaths, _parse_args, _resolve_paths


class TestParseArgs:
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

    def test_env_var_fallback(self, tmp_path):
        with patch.dict("os.environ", {"PORTAL_DATA_DIR": str(tmp_path)}):
            args = _parse_args([])
            paths = _resolve_paths(args)
            assert paths.data_dir == tmp_path

    def test_returns_build_paths(self):
        args = _parse_args([])
        paths = _resolve_paths(args)
        assert isinstance(paths, BuildPaths)

    def test_build_paths_is_frozen(self, tmp_path):
        args = _parse_args([])
        paths = _resolve_paths(args)
        with pytest.raises(FrozenInstanceError):
            paths.db_path = tmp_path / "hacked.db"  # type: ignore[misc]
