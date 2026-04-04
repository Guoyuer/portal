"""Generate a natural language narrative for a financial report.

The narrative summarizes key portfolio changes, compares to benchmarks,
and offers actionable suggestions — all from structured ReportData,
never from raw CSV (no hallucination risk on numbers).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from ..types import ReportData

log = logging.getLogger(__name__)

# Type alias: client takes a prompt string, returns a response string.
LLMClient = Callable[[str], str]

_SYSTEM_PROMPT = """\
You are a bilingual (English/Chinese) personal finance assistant.
Given structured portfolio data, write a concise 3-5 sentence narrative.

Rules:
- Lead with the most important change (total value, biggest mover)
- Compare to benchmark indices if market data is available
- Mention savings rate if cash flow data is available
- End with one actionable suggestion (rebalance, contribution target, etc.)
- Use specific numbers from the data — never invent figures
- Mix English and Chinese naturally (financial terms in English, commentary in Chinese)
- Keep it under 150 words
"""


def _build_prompt(report: ReportData) -> str:
    """Build a structured prompt from ReportData."""
    data: dict[str, Any] = {
        "date": report.date,
        "total": report.total,
        "goal": report.goal,
        "goal_pct": round(report.goal_pct, 1),
        "categories": [
            {"name": c.name, "pct": round(c.pct, 1), "target": c.target, "deviation": round(c.deviation, 1)}
            for c in report.equity_categories + report.non_equity_categories
        ],
    }

    if report.activity:
        data["activity"] = {
            "net_cash_in": report.activity.net_cash_in,
            "net_deployed": report.activity.net_deployed,
            "net_passive": report.activity.net_passive,
            "deposits": len(report.activity.deposits),
            "buys": len(report.activity.buys),
            "dividends": len(report.activity.dividends),
        }

    if report.cashflow:
        data["cashflow"] = {
            "total_income": report.cashflow.total_income,
            "total_expenses": report.cashflow.total_expenses,
            "savings_rate": round(report.cashflow.savings_rate, 1),
            "invested": report.cashflow.invested,
        }

    if report.market:
        data["market"] = {
            "indices": [{"name": i.name, "month_return": i.month_return} for i in report.market.indices],
            "portfolio_month_return": report.market.portfolio_month_return,
        }

    if report.balance_sheet:
        data["net_worth"] = report.balance_sheet.net_worth

    return _SYSTEM_PROMPT + "\n\nPortfolio data:\n```json\n" + json.dumps(data, indent=2) + "\n```"


def generate_narrative(report: ReportData, *, client: LLMClient | None = None) -> str | None:
    """Generate a narrative paragraph from ReportData.

    Args:
        report: The structured report data.
        client: Callable that takes a prompt string and returns LLM response text.
                If None, returns None (AI is optional).

    Returns:
        Narrative string or None if client is unavailable or fails.
    """
    if client is None:
        return None

    prompt = _build_prompt(report)

    try:
        response = client(prompt)
    except Exception:
        log.exception("Narrative generation failed")
        return None

    if not response or not response.strip():
        return None

    return response.strip()
