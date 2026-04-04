"""Tests for AI integration — narrative, classify, NLU.

All tests use mock clients — no real LLM API calls.
"""

from __future__ import annotations

from generate_asset_snapshot.ai.classify import classify_ticker
from generate_asset_snapshot.ai.narrative import generate_narrative
from generate_asset_snapshot.ai.nlu import parse_command
from generate_asset_snapshot.types import (
    ActivityData,
    CashFlowData,
    CashFlowItem,
    CategoryData,
    ReportData,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

CATEGORIES = ["US Equity", "Non-US Equity", "Crypto", "Safe Net", "Hedge"]


def _make_report(**overrides) -> ReportData:
    """Build a minimal ReportData for testing."""
    defaults = {
        "date": "April 02, 2026",
        "total": 100000.0,
        "total_lots": 6,
        "goal": 500000,
        "goal_pct": 20.0,
        "equity_categories": [
            CategoryData(name="US Equity", value=60000, lots=3, pct=60.0, target=60, deviation=0, is_equity=True),
        ],
        "non_equity_categories": [
            CategoryData(name="Crypto", value=15000, lots=1, pct=15.0, target=15, deviation=0, is_equity=False),
        ],
    }
    defaults.update(overrides)
    return ReportData(**defaults)


# ── Narrative tests ──────────────────────────────────────────────────────────


class TestNarrative:
    def test_returns_none_without_client(self) -> None:
        report = _make_report()
        assert generate_narrative(report) is None

    def test_generates_with_mock_client(self) -> None:
        report = _make_report()

        def client(prompt):
            return "Portfolio grew 2.4% this month."

        result = generate_narrative(report, client=client)
        assert result == "Portfolio grew 2.4% this month."

    def test_prompt_contains_report_data(self) -> None:
        captured: list[str] = []

        def mock_client(prompt: str) -> str:
            captured.append(prompt)
            return "narrative"

        report = _make_report()
        generate_narrative(report, client=mock_client)
        assert len(captured) == 1
        assert "100000" in captured[0]
        assert "US Equity" in captured[0]

    def test_includes_activity_in_prompt(self) -> None:
        captured: list[str] = []
        activity = ActivityData(
            period_start="03/01/2026",
            period_end="03/31/2026",
            deposits=[{"amount": 5000}],
            withdrawals=[],
            buys=[{"amount": -3000}],
            sells=[],
            dividends=[{"amount": 100}],
            reinvestments_total=0,
            interest_total=0,
            foreign_tax_total=0,
            net_cash_in=5000,
            net_deployed=3000,
            net_passive=100,
        )
        report = _make_report(activity=activity)
        generate_narrative(report, client=lambda p: (captured.append(p), "ok")[1])
        assert "net_cash_in" in captured[0]

    def test_includes_cashflow_in_prompt(self) -> None:
        captured: list[str] = []
        cf = CashFlowData(
            period="March 2026",
            income_items=[CashFlowItem(category="Salary", amount=8000, count=1)],
            total_income=8000,
            expense_items=[CashFlowItem(category="Rent", amount=2000, count=1)],
            total_expenses=2000,
            net_cashflow=6000,
            invested=5000,
            credit_card_payments=0,
            savings_rate=75.0,
            takehome_savings_rate=60.0,
        )
        report = _make_report(cashflow=cf)
        generate_narrative(report, client=lambda p: (captured.append(p), "ok")[1])
        assert "savings_rate" in captured[0]

    def test_handles_client_error(self) -> None:
        def failing_client(prompt: str) -> str:
            raise RuntimeError("API down")

        report = _make_report()
        result = generate_narrative(report, client=failing_client)
        assert result is None

    def test_narrative_returns_none_on_empty_response(self) -> None:
        """Returns None if the LLM returns empty/whitespace."""
        report = _make_report()

        def empty_client(prompt: str) -> str:
            return "   "

        result = generate_narrative(report, client=empty_client)
        assert result is None

    def test_narrative_strips_response(self) -> None:
        """Strips leading/trailing whitespace from narrative."""
        report = _make_report()

        def padded_client(prompt: str) -> str:
            return "  Portfolio is strong.  "

        result = generate_narrative(report, client=padded_client)
        assert result == "Portfolio is strong."


# ── Classify tests ───────────────────────────────────────────────────────────


class TestClassify:
    def test_returns_none_without_client(self) -> None:
        assert classify_ticker("AMZN", "AMAZON.COM INC", CATEGORIES) is None

    def test_classifies_with_mock(self) -> None:
        def client(prompt):
            return '{"category": "US Equity", "subtype": "growth", "source": "fidelity"}'

        result = classify_ticker("AMZN", "AMAZON.COM INC", CATEGORIES, client=client)
        assert result is not None
        assert result["category"] == "US Equity"
        assert result["subtype"] == "growth"

    def test_prompt_contains_context(self) -> None:
        captured: list[str] = []

        def mock(prompt: str) -> str:
            captured.append(prompt)
            return '{"category": "US Equity", "subtype": "growth"}'

        classify_ticker("TSLA", "TESLA INC", CATEGORIES, client=mock)
        assert "TSLA" in captured[0]
        assert "TESLA INC" in captured[0]
        assert "US Equity" in captured[0]
        assert "Crypto" in captured[0]

    def test_rejects_invalid_category(self) -> None:
        def client(prompt):
            return '{"category": "Space Mining", "subtype": "other"}'

        result = classify_ticker("XYZ", "Unknown Corp", CATEGORIES, client=client)
        assert result is None

    def test_handles_malformed_json(self) -> None:
        def client(prompt):
            return "I think this is a tech stock"

        result = classify_ticker("XYZ", "Unknown", CATEGORIES, client=client)
        assert result is None

    def test_handles_client_error(self) -> None:
        def failing(prompt: str) -> str:
            raise RuntimeError("API error")

        result = classify_ticker("XYZ", "Unknown", CATEGORIES, client=failing)
        assert result is None

    def test_handles_markdown_code_block(self) -> None:
        def client(prompt):
            return '```json\n{"category": "Crypto", "subtype": "other"}\n```'

        result = classify_ticker("DOGE", "Dogecoin", CATEGORIES, client=client)
        assert result is not None
        assert result["category"] == "Crypto"

    def test_classify_extracts_json_from_prose(self) -> None:
        """Extracts JSON even when LLM wraps it in prose text."""

        def wordy_client(prompt: str) -> str:
            return (
                "Based on my analysis, the classification is:\n"
                '{"category": "Crypto", "subtype": "token"}\n'
                "This is because it trades on crypto exchanges."
            )

        result = classify_ticker("BTC", "BITCOIN", CATEGORIES, client=wordy_client)
        assert result is not None
        assert result["category"] == "Crypto"
        assert result["subtype"] == "token"


# ── NLU tests ────────────────────────────────────────────────────────────────


class TestNLU:
    def test_contribute_english(self) -> None:
        result = parse_command("invest 5000")
        assert result is not None
        assert result["action"] == "contribute"
        assert result["amount"] == 5000.0

    def test_contribute_with_dollar(self) -> None:
        result = parse_command("contribute $10,000")
        assert result is not None
        assert result["amount"] == 10000.0

    def test_contribute_chinese(self) -> None:
        result = parse_command("帮我算一下如果投 3000 怎么分配")
        assert result is not None
        assert result["action"] == "contribute"
        assert result["amount"] == 3000.0

    def test_allocate_chinese(self) -> None:
        result = parse_command("分配 5000")
        assert result is not None
        assert result["amount"] == 5000.0

    def test_update_manual_english(self) -> None:
        result = parse_command("I Bonds now worth 23500")
        assert result is not None
        assert result["action"] == "update_manual"
        assert result["asset"] == "I Bonds"
        assert result["value"] == 23500.0

    def test_update_manual_chinese(self) -> None:
        result = parse_command("I Bonds 现在值 23500")
        assert result is not None
        assert result["action"] == "update_manual"

    def test_query(self) -> None:
        result = parse_command("show my dividends")
        assert result is not None
        assert result["action"] == "query"
        assert "dividends" in result["subject"]

    def test_returns_none_for_empty(self) -> None:
        assert parse_command("") is None
        assert parse_command(None) is None

    def test_returns_none_for_unknown_without_client(self) -> None:
        result = parse_command("hello there")
        assert result is None

    def test_llm_fallback(self) -> None:
        def client(prompt):
            return '{"action": "abbreviate", "format": "summary"}'

        result = parse_command("简短版就好", client=client)
        assert result is not None
        assert result["action"] == "abbreviate"

    def test_llm_returns_null_action(self) -> None:
        def client(prompt):
            return '{"action": null}'

        result = parse_command("hello", client=client)
        assert result is None

    def test_llm_error_returns_none(self) -> None:
        def failing(prompt: str) -> str:
            raise RuntimeError("down")

        result = parse_command("some command", client=failing)
        assert result is None

    def test_parse_command_extracts_json_from_prose(self) -> None:
        """Extracts JSON even when LLM wraps it in explanation."""

        def wordy_client(prompt: str) -> str:
            return (
                "The user wants to contribute money. Here is the parsed command:\n"
                '{"action": "contribute", "amount": 10000}\n'
                "This means they want to invest $10,000."
            )

        result = parse_command("put in 10000", client=wordy_client)
        assert result is not None
        assert result["action"] == "contribute"
        assert result["amount"] == 10000

    def test_parse_command_prompt_includes_input(self) -> None:
        """The user text is included in the prompt sent to the LLM."""
        captured: list[str] = []

        def capturing_client(prompt: str) -> str:
            captured.append(prompt)
            return '{"action": "contribute", "amount": 5000}'

        parse_command("buy some stocks please", client=capturing_client)
        assert len(captured) == 1
        assert "buy some stocks please" in captured[0]

    def test_parse_command_unknown_with_client(self) -> None:
        """Returns None when LLM cannot parse the input into a command."""

        def client(prompt: str) -> str:
            return "I cannot understand this request."

        result = parse_command("asdfghjkl random gibberish", client=client)
        assert result is None
