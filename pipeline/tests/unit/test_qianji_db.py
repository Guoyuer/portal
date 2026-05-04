"""Unit tests for Qianji ingest."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from etl.qianji.balances import _load_balances
from etl.qianji.currency import parse_qj_amount, parse_qj_target_amount
from etl.qianji.ingest import _load_records, ingest_qianji_transactions
from tests.fixtures import db_rows, db_value

# ── parse_qj_amount ───────────────────────────────────────────────────────────


def _extra(ss: str, sv: float, ts: str | None, tv: float, bs: str, bv: float) -> str:
    return json.dumps({"curr": {"ss": ss, "sv": sv, "ts": ts, "tv": tv, "bs": bs, "bv": bv}})


def _flag_for(db_path: Path) -> int:
    return db_value(db_path, "SELECT is_retirement FROM qianji_transactions")  # type: ignore[return-value]


def _row_for(db_path: Path) -> tuple:
    return db_rows(db_path, "SELECT type, account_to FROM qianji_transactions")[0]


class TestParseQjAmount:
    @pytest.mark.parametrize(
        ("money", "extra", "expected"),
        [
            pytest.param(100.0, "null", 100.0, id="null-string"),
            pytest.param(100.0, None, 100.0, id="none"),
            pytest.param(2590.52, _extra("CNY", 2590.52, None, 0.0, "USD", 358.0), 358.0, id="cny-expense-bv"),
            pytest.param(5000.0, _extra("CNY", 5000.0, "USD", 692.0, "USD", 692.0), 692.0, id="cny-usd-transfer"),
            pytest.param(10000.0, _extra("CNY", 10000.0, "CNY", 10000.0, "USD", 1385.0), 1385.0, id="cny-cny-bv"),
            pytest.param(7000.0, _extra("CNY", 7000.0, "CNY", 7000.0, "USD", 7000.0), 7000.0, id="cny-cny-unconverted"),
            pytest.param(2000.0, _extra("USD", 2000.0, "CNY", 14366.0, "USD", 2000.0), 2000.0, id="usd-source"),
            pytest.param(50.0, "not json", 50.0, id="malformed-json"),
            pytest.param(50.0, json.dumps({"tags": None}), 50.0, id="missing-curr"),
            pytest.param(100.0, json.dumps({"curr": {"ss": "CNY", "sv": 100.0, "bs": "USD", "bv": None}}), 100.0, id="bv-none"),
            pytest.param(50.0, json.dumps({"curr": 123}), 50.0, id="curr-int"),
            pytest.param(50.0, json.dumps({"curr": ["CNY"]}), 50.0, id="curr-list"),
            pytest.param(50.0, json.dumps({"curr": {"ss": "CNY"}}), 50.0, id="curr-missing-bs"),
            pytest.param(50.0, json.dumps({"curr": {"ss": "CNY", "bs": "USD"}}), 50.0, id="curr-missing-bv"),
            pytest.param(50.0, json.dumps({"curr": {"ss": "CNY", "bs": "USD", "bv": 7.0}}), 50.0, id="curr-missing-sv"),
            pytest.param(100.0, _extra("USD", 100.0, None, 0.0, "USD", 100.0), 100.0, id="same-currency"),
            pytest.param(100.0, _extra("CNY", 100.0, None, 0.0, "USD", 100.005), 100.0, id="bv-sv-tolerance"),
            pytest.param(50.0, "", 50.0, id="empty-string"),
        ],
    )
    def test_amount_parser_cases(self, money: float, extra: str | None, expected: float) -> None:
        assert parse_qj_amount(money, extra) == expected

    @pytest.mark.parametrize(
        ("money", "extra", "cny_rate", "expected"),
        [
            pytest.param(
                5000.0,
                _extra("CNY", 5000.0, None, 0.0, "USD", 5000.0),
                7.0,
                pytest.approx(714.2857, rel=1e-3),
                id="cny-rate-converts",
            ),
            pytest.param(
                5000.0,
                _extra("CNY", 5000.0, None, 0.0, "USD", 5000.0),
                None,
                5000.0,
                id="cny-no-rate",
            ),
            pytest.param(
                100.0,
                _extra("EUR", 100.0, None, 0.0, "USD", 100.0),
                7.0,
                100.0,
                id="non-cny-rate-ignored",
            ),
            pytest.param(
                5000.0,
                _extra("CNY", 5000.0, None, 0.0, "USD", 5000.0),
                0.0,
                5000.0,
                id="zero-rate",
            ),
        ],
    )
    def test_unconverted_label_quirks(
        self,
        money: float,
        extra: str,
        cny_rate: float | None,
        expected: object,
    ) -> None:
        assert parse_qj_amount(money, extra, cny_rate=cny_rate) == expected

    def test_unconverted_cny_uses_historical_rate_over_live_rate(self):
        extra = _extra("CNY", 7000.0, None, 0.0, "USD", 7000.0)
        bill_date = date(2024, 5, 18)
        historical = {bill_date: 7.2345, date(2026, 4, 18): 6.8164}

        run1 = parse_qj_amount(7000.0, extra, cny_rate=6.8164,
                               bill_date=bill_date, historical_cny_rates=historical)
        run2 = parse_qj_amount(7000.0, extra, cny_rate=6.9003,
                               bill_date=bill_date, historical_cny_rates=historical)
        assert run1 == run2
        assert run1 == pytest.approx(7000.0 / 7.2345, rel=1e-6)

    @pytest.mark.parametrize(
        ("amount", "bill_date", "historical", "cny_rate", "expected_rate"),
        [
            (1000.0, date(2024, 5, 18), {date(2024, 5, 17): 7.2345}, None, 7.2345),
            (5000.0, date(2024, 5, 18), {}, 7.0, 7.0),
            (5000.0, None, {}, 7.0, 7.0),
        ],
    )
    def test_unconverted_cny_rate_fallbacks(
        self,
        amount: float,
        bill_date: date | None,
        historical: dict[date, float],
        cny_rate: float | None,
        expected_rate: float,
    ) -> None:
        extra = _extra("CNY", amount, None, 0.0, "USD", amount)
        assert parse_qj_amount(
            amount, extra, cny_rate=cny_rate, bill_date=bill_date, historical_cny_rates=historical,
        ) == pytest.approx(amount / expected_rate, rel=1e-6)


# ── parse_qj_target_amount ────────────────────────────────────────────────────


class TestParseQjTargetAmount:
    @pytest.mark.parametrize(
        ("extra", "expected"),
        [
            pytest.param(None, 100.0, id="none"),
            pytest.param("null", 100.0, id="null"),
            pytest.param("not json", 100.0, id="invalid-json"),
            pytest.param('{"curr": {"ss": "USD", "ts": "USD", "tv": 100}}', 100.0, id="same-currency"),
            pytest.param('{"curr": {"ss": "USD", "ts": "CNY", "tv": 723.5}}', 723.5, id="cross-currency"),
            pytest.param('{"other": "data"}', 100.0, id="missing-curr"),
            pytest.param('{"curr": {"ss": "USD", "ts": "CNY", "tv": 0}}', 100.0, id="tv-zero"),
        ],
    )
    def test_target_amount_cases(self, extra: str | None, expected: float) -> None:
        assert parse_qj_target_amount(100.0, extra) == expected


# ── _load_records ─────────────────────────────────────────────────────────────


def _bill(
    id_: int,
    type_: int = 0,
    money: float = 10.0,
    fromact: str | None = "A",
    targetact: str | None = None,
    remark: str | None = None,
    ts: int | None = None,
    cateid: int | None = None,
    extra: str = "null",
    status: int = 1,
) -> tuple:
    timestamp = ts if ts is not None else int(datetime(2025, 1, 1, tzinfo=UTC).timestamp())
    return (id_, type_, money, fromact, targetact, remark, timestamp, cateid, extra, status)


def _make_db(conn: sqlite3.Connection, bills: list[tuple], categories: list[tuple] | None = None) -> None:
    conn.execute(
        "CREATE TABLE category (id INTEGER PRIMARY KEY, name TEXT)"
    )
    conn.execute(
        "CREATE TABLE user_bill ("
        "id INTEGER PRIMARY KEY, type INTEGER, money NUMBER NOT NULL, "
        "fromact TEXT, targetact TEXT, remark TEXT, time INTEGER, "
        "cateid INTEGER, extra TEXT, status INTEGER DEFAULT 1)"
    )
    for cat_id, cat_name in (categories or []):
        conn.execute("INSERT INTO category VALUES (?, ?)", (cat_id, cat_name))
    conn.executemany(
        "INSERT INTO user_bill (id, type, money, fromact, targetact, remark, time, cateid, extra, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        bills,
    )


def _records_for(
    bills: list[tuple],
    categories: list[tuple] | None = None,
    **kwargs: object,
) -> list[dict]:
    conn = sqlite3.connect(":memory:")
    try:
        _make_db(conn, bills, categories)
        kwargs.setdefault("historical_cny_rates", {})
        return _load_records(conn, **kwargs)
    finally:
        conn.close()


class TestLoadRecords:
    def test_basic_expense(self):
        ts = int(datetime(2025, 1, 15, 12, 0, tzinfo=UTC).timestamp())
        records = _records_for(
            [_bill(1, money=50.0, fromact="Chase Debit", remark="lunch", ts=ts, cateid=10)],
            [(10, "Food")],
        )
        assert len(records) == 1
        r = records[0]
        assert r["type"] == "expense"
        assert r["amount"] == 50.0
        assert r["category"] == "Food"
        assert r["account_from"] == "Chase Debit"
        assert r["account_to"] == ""
        assert r["note"] == "lunch"

    def test_null_fields_become_empty_string(self):
        records = _records_for([_bill(1, fromact=None)])
        r = records[0]
        assert r["account_from"] == ""
        assert r["account_to"] == ""
        assert r["note"] == ""
        assert r["category"] == ""

    def test_unknown_type_skipped(self):
        records = _records_for([
            _bill(1, type_=0, fromact="A"),
            _bill(2, type_=99, money=20.0, fromact="B"),
        ])
        assert len(records) == 1
        assert records[0]["amount"] == 10.0

    def test_all_four_types(self):
        records = _records_for([
            _bill(1, type_=0, money=10.0),
            _bill(2, type_=1, money=20.0),
            _bill(3, type_=2, money=30.0, targetact="B"),
            _bill(4, type_=3, money=40.0),
        ])
        types = [r["type"] for r in records]
        assert types == ["expense", "income", "transfer", "repayment"]

    def test_cny_amount_converted(self):
        extra = _extra("CNY", 1000.0, None, 0.0, "USD", 139.0)
        records = _records_for([_bill(1, money=1000.0, fromact="Alipay", extra=extra)])
        assert records[0]["amount"] == 139.0

    def test_inactive_bills_excluded(self):
        records = _records_for([
            _bill(1, remark="active"),
            _bill(2, money=20.0, remark="deleted", status=0),
        ])
        assert len(records) == 1
        assert records[0]["note"] == "active"

    def test_cny_rate_passed_through_to_parse_qj_amount(self):
        extra = _extra("CNY", 5000.0, None, 0.0, "USD", 5000.0)
        bills = [_bill(1, money=5000.0, fromact="Alipay", extra=extra)]
        assert _records_for(bills)[0]["amount"] == 5000.0
        assert _records_for(bills, cny_rate=7.0)[0]["amount"] == pytest.approx(714.2857, rel=1e-3)

    def test_balance_adjustment_rows_skipped(self):
        ts = int(datetime(2025, 1, 15, 12, 0, tzinfo=UTC).timestamp())
        records = _records_for([
            _bill(1, remark="groceries", ts=ts),
            _bill(2, money=500.0, remark="Balance adjustment(29,338.34 ~ 25,524.00)", ts=ts),
            _bill(3, money=6.0, remark="adjust", ts=ts),
            _bill(4, money=7.0, remark="ADJUST", ts=ts),
            _bill(5, money=8.0, remark="  balance adjustment (y~x)", ts=ts),
            _bill(6, money=9.0, remark="maladjusted dinner", ts=ts),
        ])
        notes = [r["note"] for r in records]
        assert "groceries" in notes
        assert "maladjusted dinner" in notes
        assert not any("adjustment" in n.lower() or n.lower().strip() == "adjust" for n in notes)
        assert len(records) == 2

    def test_date_truncation_uses_user_timezone(self):
        ts = int(datetime(2026, 4, 10, 6, 30, tzinfo=UTC).timestamp())
        records = _records_for([_bill(1, money=15.0, remark="late-night snack", ts=ts)])
        assert records[0]["date"].startswith("2026-04-09")


# ── _load_balances ────────────────────────────────────────────────────────────


def _balances_for(rows: list[tuple[str, float, str | None, int]]) -> dict[str, tuple[float, str]]:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE user_asset (name TEXT, money NUMBER, currency TEXT, status INTEGER)")
        conn.executemany("INSERT INTO user_asset VALUES (?, ?, ?, ?)", rows)
        return _load_balances(conn)
    finally:
        conn.close()


class TestLoadBalances:
    def test_loads_active_balances_and_currencies(self):
        balances = _balances_for([
            ("Chase", 5000.50, "USD", 0),
            ("Alipay", 70000, "CNY", 0),
            ("Old", 100, None, 0),
            ("Closed", 0, "USD", 2),
        ])
        assert balances["Chase"] == (5000.50, "USD")
        assert balances["Alipay"] == (70000, "CNY")
        assert balances["Old"] == (100, "USD")
        assert "Chase" in balances
        assert "Closed" not in balances


# ── ingest_qianji_transactions — DB writes ────────────────────────────────────


def _record_dict(
    *,
    date_: str = "2026-01-01",
    type_: str = "income",
    category: str = "Salary",
    amount: float = 1000.0,
    account_from: str = "",
    account_to: str = "",
    note: str = "",
) -> dict:
    return {
        "date": date_, "type": type_, "category": category, "amount": amount,
        "account_from": account_from, "account_to": account_to, "note": note,
    }


class TestIngestQianjiTransactions:
    def test_ingest_records(self, empty_db: Path) -> None:
        records = [
            _record_dict(date_="2025-03-01", category="Salary", amount=5000.0, account_from="Checking"),
            _record_dict(date_="2025-03-05", type_="expense", category="Rent", amount=1500.0, account_from="Checking"),
        ]
        assert ingest_qianji_transactions(empty_db, records, retirement_categories=[]) == 2

    def test_clears_and_replaces(self, empty_db: Path) -> None:
        ingest_qianji_transactions(
            empty_db,
            [_record_dict(date_="2025-03-01", amount=5000.0, account_from="Checking")],
            retirement_categories=[],
        )
        new = [_record_dict(date_="2025-04-01", type_="expense", category="Food", amount=100.0, account_from="Checking")]
        assert ingest_qianji_transactions(empty_db, new, retirement_categories=[]) == 1

    def test_empty_records(self, empty_db: Path) -> None:
        assert ingest_qianji_transactions(empty_db, [], retirement_categories=[]) == 0


# ── Retirement flag — ingest_qianji_transactions ──────────────────────────────


_SAMPLE_RETIREMENT_RECORDS = [
    _record_dict(date_="2026-01-28", category="Salary", amount=8000),
    _record_dict(date_="2026-01-28", category="401K", amount=1600),
    _record_dict(date_="2026-01-10", type_="expense", category="Rent", amount=2000),
]


class TestIsRetirementFlag:
    def test_default_config_matches_401k_income(self, empty_db: Path) -> None:
        ingest_qianji_transactions(
            empty_db, _SAMPLE_RETIREMENT_RECORDS,
            retirement_categories=["401K", "401k Match"],
        )
        rows = db_rows(
            empty_db,
            "SELECT category, type, is_retirement FROM qianji_transactions ORDER BY date, category",
        )
        assert ("401K", "income", 1) in rows
        assert ("Salary", "income", 0) in rows
        assert ("Rent", "expense", 0) in rows

    @pytest.mark.parametrize(
        ("record", "retirement_categories", "expected_flag"),
        [
            pytest.param(
                _record_dict(type_="expense", category="401K", amount=100),
                ["401K"], 0,
                id="expense-not-flagged-even-if-category-matches",
            ),
            pytest.param(
                _record_dict(category="401k", amount=1000),
                ["401K"], 0,
                id="case-sensitive-mismatch",
            ),
        ],
    )
    def test_single_record_flag(
        self,
        empty_db: Path,
        record: dict,
        retirement_categories: list[str],
        expected_flag: int,
    ) -> None:
        ingest_qianji_transactions(empty_db, [record], retirement_categories=retirement_categories)
        assert _flag_for(empty_db) == expected_flag

    def test_empty_retirement_list_flags_nothing(self, empty_db: Path) -> None:
        ingest_qianji_transactions(empty_db, _SAMPLE_RETIREMENT_RECORDS, retirement_categories=[])
        assert db_value(empty_db, "SELECT COUNT(*) FROM qianji_transactions WHERE is_retirement = 1") == 0


class TestAccountToNormalization:
    @pytest.mark.parametrize(
        ("record", "expected"),
        [
            pytest.param(
                _record_dict(type_="transfer", category="", amount=1000,
                             account_from="Chase Debit", account_to="Fidelity taxable"),
                ("transfer", "Fidelity taxable"),
                id="transfer-uses-account-to",
            ),
            pytest.param(
                _record_dict(type_="income", category="Salary", amount=3000,
                             account_from="Fidelity taxable"),
                ("income", "Fidelity taxable"),
                id="income-uses-account-from-as-destination",
            ),
            pytest.param(
                _record_dict(type_="expense", category="Rent", amount=2000,
                             account_from="Chase Debit"),
                ("expense", ""),
                id="expense-account-to-defaults-to-empty",
            ),
        ],
    )
    def test_destination_account(self, empty_db: Path, record: dict, expected: tuple) -> None:
        ingest_qianji_transactions(empty_db, [record], retirement_categories=[])
        assert _row_for(empty_db) == expected
