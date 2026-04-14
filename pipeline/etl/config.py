"""Configuration loading and validation."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .types import EQUITY_CATEGORIES, Config, ConfigError

log = logging.getLogger(__name__)


def validate_config(data: dict[str, object]) -> list[str]:
    """Validate config and return list of error strings (empty = valid)."""
    errors: list[str] = []

    for key, expected in [("assets", dict), ("target_weights", dict)]:
        if key not in data:
            errors.append(f"Missing required field: '{key}'")
        elif not isinstance(data[key], expected):
            errors.append(f"'{key}' must be a {expected.__name__}")
    if errors:
        return errors

    # isinstance checks above confirmed both are dicts; re-assert for mypy
    # (inline narrowing doesn't survive the intermediate `if errors: return`).
    assets = data["assets"]
    weights = data["target_weights"]
    assert isinstance(assets, dict)
    assert isinstance(weights, dict)
    order = data.get("category_order", [])

    for ticker, info in assets.items():
        if not isinstance(info, dict):
            errors.append(f"Asset '{ticker}': must be a dict")
            continue
        cat = info.get("category")
        if not cat:
            errors.append(f"Asset '{ticker}': missing 'category'")
        elif cat not in weights:
            errors.append(f"Asset '{ticker}': category '{cat}' not in target_weights")
        if cat in EQUITY_CATEGORIES and not info.get("subtype"):
            errors.append(f"Asset '{ticker}': {cat} requires 'subtype' (broad/growth)")

    for cat, w in weights.items():
        if not isinstance(w, (int, float)) or w < 0:
            errors.append(f"Weight '{cat}': must be number >= 0")
    total = sum(w for w in weights.values() if isinstance(w, (int, float)))
    if abs(total - 100) > 0.01:
        errors.append(f"Weights sum to {total}%, expected 100%")

    cats_with_assets = {info.get("category") for info in assets.values() if isinstance(info, dict)}
    for cat in weights:
        if cat not in cats_with_assets:
            errors.append(f"Category '{cat}' has no assets")

    if isinstance(order, list):
        for cat in order:
            if cat not in weights:
                errors.append(f"'{cat}' in category_order not in target_weights")
        missing = set(weights) - set(order)
        if missing:
            errors.append(f"Missing from category_order: {', '.join(sorted(missing))}")

    if "goal" in data:
        goal = data["goal"]
        if not isinstance(goal, (int, float)) or goal <= 0:
            errors.append("'goal' must be a positive number")

    return errors


def load_config(path: Path) -> Config:
    """Load and validate config file. Raises ConfigError on error."""
    if not path.exists():
        raise ConfigError(f"Config not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON: {e}") from e

    if errors := validate_config(data):
        raise ConfigError("Config errors:\n  - " + "\n  - ".join(errors))

    cfg = Config(
        assets=data["assets"].copy(),
        weights=data["target_weights"],
        order=data.get("category_order", list(data["target_weights"].keys())),
        aliases=data.get("aliases", {}),
        goal=data.get("goal", 0),
        qianji_accounts=data.get("qianji_accounts", {}),
        fidelity_accounts=data.get("fidelity_accounts", {}),
    )
    log.info("Config: %d assets, %d categories, goal $%s", len(data["assets"]), len(data["target_weights"]), f"{data.get('goal', 0):,.0f}")
    return cfg


