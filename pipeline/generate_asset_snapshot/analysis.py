"""Pure computation helpers — no I/O or display logic."""

from __future__ import annotations

from collections import defaultdict

from .types import Config, Portfolio


def pct(value: float, total: float) -> float:
    """Return value as a percentage of total, or 0 if total is zero."""
    return (value / total * 100) if total > 0 else 0.0


def get_tickers(portfolio: Portfolio, config: Config, category: str) -> list[str]:
    """Return tickers in *category*, sorted by value descending."""
    return sorted(
        [t for t in portfolio["totals"] if config["assets"].get(t, {}).get("category") == category],
        key=lambda t: portfolio["totals"][t],
        reverse=True,
    )


def group_by_subtype(tickers: list[str], config: Config) -> dict[str, list[str]]:
    """Group tickers by their subtype (broad/growth/other)."""
    groups: dict[str, list[str]] = defaultdict(list)
    for t in tickers:
        subtype = config["assets"].get(t, {}).get("subtype") or "other"
        groups[subtype].append(t)
    return groups


def cat_value(portfolio: Portfolio, config: Config, category: str) -> float:
    """Total market value of all tickers in *category*."""
    return sum(portfolio["totals"][t] for t in get_tickers(portfolio, config, category))


def calculate_allocation(portfolio: Portfolio, config: Config, amount: float) -> dict[str, float]:
    """Calculate optimal contribution allocation to rebalance toward targets.

    Returns {category: allocated_amount}.
    """
    new_total = portfolio["total"] + amount

    categories: list[tuple[str, float, float, float]] = []
    for cat, target in config["weights"].items():
        current = cat_value(portfolio, config, cat)
        gap = target - pct(current, portfolio["total"])
        categories.append((cat, current, target, gap))

    allocation: dict[str, float] = {cat: 0.0 for cat, _, _, _ in categories}
    remaining = amount

    for cat, current, target, gap in sorted(categories, key=lambda x: x[3], reverse=True):
        if remaining <= 0:
            break
        if gap <= 0:
            continue
        target_value = new_total * target / 100
        needed = target_value - current
        alloc = min(needed, remaining)
        if alloc > 0:
            allocation[cat] = alloc
            remaining -= alloc

    if remaining > 0:
        for cat, _, target, _ in categories:
            allocation[cat] += remaining * target / 100

    return allocation
