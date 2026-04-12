"""Tests for config loading and validation."""

from __future__ import annotations

import pytest

from etl.config import load_config, validate_config
from etl.types import ConfigError

from .conftest import load_test_config


class TestValidateConfig:
    def test_valid_config(self, config_data):
        assert validate_config(config_data) == []

    def test_missing_assets(self, config_data):
        del config_data["assets"]
        errors = validate_config(config_data)
        assert any("Missing required field" in e and "assets" in e for e in errors)

    def test_missing_target_weights(self, config_data):
        del config_data["target_weights"]
        errors = validate_config(config_data)
        assert any("target_weights" in e for e in errors)

    def test_weights_dont_sum_to_100(self, config_data):
        config_data["target_weights"]["US Equity"] = 90
        errors = validate_config(config_data)
        assert any("sum to" in e for e in errors)

    def test_asset_missing_category(self, config_data):
        config_data["assets"]["BAD"] = {"subtype": "broad"}
        errors = validate_config(config_data)
        assert any("BAD" in e and "category" in e for e in errors)

    def test_asset_category_not_in_weights(self, config_data):
        config_data["assets"]["BAD"] = {"category": "Nonexistent"}
        errors = validate_config(config_data)
        assert any("Nonexistent" in e and "not in target_weights" in e for e in errors)

    def test_equity_missing_subtype(self, config_data):
        config_data["assets"]["NOSUBTYPE"] = {"category": "US Equity"}
        errors = validate_config(config_data)
        assert any("subtype" in e for e in errors)

    def test_non_equity_no_subtype_required(self, config_data):
        """Non-equity categories should NOT require subtype."""
        assert validate_config(config_data) == []

    def test_negative_weight(self, config_data):
        config_data["target_weights"]["Crypto"] = -5
        errors = validate_config(config_data)
        assert any("must be number >= 0" in e for e in errors)

    def test_category_order_missing_entries(self, config_data):
        config_data["category_order"] = ["US Equity"]
        errors = validate_config(config_data)
        assert any("Missing from category_order" in e for e in errors)

    def test_category_with_no_assets(self, config_data):
        config_data["target_weights"]["Empty Cat"] = 0
        config_data["target_weights"]["US Equity"] = 55
        config_data["category_order"].append("Empty Cat")
        errors = validate_config(config_data)
        assert any("Empty Cat" in e and "no assets" in e for e in errors)

    def test_goal_must_be_positive(self, config_data):
        config_data["goal"] = -1
        errors = validate_config(config_data)
        assert any("goal" in e and "positive" in e for e in errors)

    def test_goal_zero_is_invalid(self, config_data):
        config_data["goal"] = 0
        errors = validate_config(config_data)
        assert any("goal" in e for e in errors)

    def test_valid_goal(self, config_data):
        config_data["goal"] = 2_000_000
        assert validate_config(config_data) == []


class TestLoadConfig:
    def test_loads_valid_config(self, config):
        assert "assets" in config
        assert "weights" in config
        assert "order" in config
        assert "aliases" in config
        assert "goal" in config

    def test_missing_file(self, tmp_path):
        with pytest.raises(ConfigError, match="Config not found"):
            load_config(tmp_path / "nope.json")

    def test_invalid_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{invalid json")
        with pytest.raises(ConfigError, match="Invalid JSON"):
            load_config(p)

    def test_validation_errors_cause_exit(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text('{"assets": {}}')
        with pytest.raises(ConfigError, match="Config errors"):
            load_config(p)

    def test_aliases_loaded(self, tmp_path, config_data):
        config_data["aliases"] = {"Long Name Fund": "VOO"}
        config = load_test_config(tmp_path, config_data)
        assert config["aliases"]["Long Name Fund"] == "VOO"

    def test_goal_loaded(self, tmp_path, config_data):
        config_data["goal"] = 2_000_000
        config = load_test_config(tmp_path, config_data)
        assert config["goal"] == 2_000_000

    def test_goal_defaults_to_zero(self, config):
        assert config["goal"] == 0
