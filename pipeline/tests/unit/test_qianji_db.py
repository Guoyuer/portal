"""Unit tests for qianji_db ingest — _parse_amount, _load_records, _load_balances."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from generate_asset_snapshot.db import ingest_qianji_transactions, init_db
from generate_asset_snapshot.ingest.qianji_db import _load_balances, _load_records, _parse_amount

# ── _parse_amount ─────────────────────────────────────────────────────────────


def _extra(ss: str, sv: float, ts: str | None, tv: float, bs: str, bv: float) -> str:
    return json.dumps({"curr": {"ss": ss, "sv": sv, "ts": ts, "tv": tv, "bs": bs, "bv": bv}})


class TestParseAmount:
    def test_null_string_returns_money(self):
        assert _parse_amount(100.0, "null") == 100.0

    def test_none_returns_money(self):
        assert _parse_amount(100.0, None) == 100.0

    def test_cny_expense_uses_bv(self):
        """CNY expense with ts=None: money=2590.52 (CNY), bv=358.0 (USD)."""
        extra = _extra("CNY", 2590.52, None, 0.0, "USD", 358.0)
        assert _parse_amount(2590.52, extra) == 358.0

    def test_cny_to_usd_transfer_uses_bv(self):
        """CNY→USD transfer: money=5000 (CNY), bv=692 (USD)."""
        extra = _extra("CNY", 5000.0, "USD", 692.0, "USD", 692.0)
        assert _parse_amount(5000.0, extra) == 692.0

    def test_cny_to_cny_transfer_bv_converted(self):
        """CNY→CNY transfer where bv has USD conversion (bv != sv)."""
        extra = _extra("CNY", 10000.0, "CNY", 10000.0, "USD", 1385.0)
        assert _parse_amount(10000.0, extra) == 1385.0

    def test_cny_to_cny_transfer_bv_equals_sv(self):
        """CNY→CNY transfer where bv == sv (no conversion happened).

        Should fall back to money since bv == sv means no real conversion.
        """
        extra = _extra("CNY", 7000.0, "CNY", 7000.0, "USD", 7000.0)
        # bv == sv → no conversion detected → fallback to money
        assert _parse_amount(7000.0, extra) == 7000.0

    def test_usd_source_falls_back_to_money(self):
        """USD→CNY transfer: ss=USD, code should use money directly."""
        extra = _extra("USD", 2000.0, "CNY", 14366.0, "USD", 2000.0)
        assert _parse_amount(2000.0, extra) == 2000.0

    def test_malformed_json_returns_money(self):
        assert _parse_amount(50.0, "not json") == 50.0

    def test_extra_without_curr_returns_money(self):
        assert _parse_amount(50.0, json.dumps({"tags": None})) == 50.0

    def test_bv_none_returns_money(self):
        extra = json.dumps({"curr": {"ss": "CNY", "sv": 100.0, "bs": "USD", "bv": None}})
        assert _parse_amount(100.0, extra) == 100.0

    def test_curr_not_dict_returns_money(self):
        """curr present but not a dict (e.g. int, list)."""
        assert _parse_amount(50.0, json.dumps({"curr": 123})) == 50.0
        assert _parse_amount(50.0, json.dumps({"curr": ["CNY"]})) == 50.0

    def test_curr_missing_fields_returns_money(self):
        """curr dict but missing required fields."""
        assert _parse_amount(50.0, json.dumps({"curr": {"ss": "CNY"}})) == 50.0
        assert _parse_amount(50.0, json.dumps({"curr": {"ss": "CNY", "bs": "USD"}})) == 50.0
        assert _parse_amount(50.0, json.dumps({"curr": {"ss": "CNY", "bs": "USD", "bv": 7.0}})) == 50.0

    def test_ss_equals_bs_returns_money(self):
        """Same source and base currency — no conversion needed."""
        extra = _extra("USD", 100.0, None, 0.0, "USD", 100.0)
        assert _parse_amount(100.0, extra) == 100.0

    def test_bv_sv_within_tolerance_returns_money(self):
        """bv and sv differ by less than tolerance — treat as unconverted."""
        extra = _extra("CNY", 100.0, None, 0.0, "USD", 100.005)
        assert _parse_amount(100.0, extra) == 100.0

    def test_empty_string_returns_money(self):
        assert _parse_amount(50.0, "") == 50.0


# ── _load_records ─────────────────────────────────────────────────────────────


def _make_db(conn: sqlite3.Connection, bills: list[tuple], categories: list[tuple] | None = None):
    """Create user_bill and category tables with test data."""
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
    for bill in bills:
        conn.execute(
            "INSERT INTO user_bill (id, type, money, fromact, targetact, remark, time, cateid, extra, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            bill,
        )


class TestLoadRecords:
    def test_basic_expense(self):
        conn = sqlite3.connect(":memory:")
        ts = int(datetime(2025, 1, 15, 12, 0, tzinfo=UTC).timestamp())
        _make_db(conn, [(1, 0, 50.0, "Chase Debit", None, "lunch", ts, 10, "null", 1)], [(10, "Food")])
        records = _load_records(conn)
        assert len(records) == 1
        r = records[0]
        assert r["type"] == "expense"
        assert r["amount"] == 50.0
        assert r["category"] == "Food"
        assert r["account_from"] == "Chase Debit"
        assert r["account_to"] == ""
        assert r["note"] == "lunch"

    def test_null_fields_become_empty_string(self):
        conn = sqlite3.connect(":memory:")
        ts = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp())
        _make_db(conn, [(1, 0, 10.0, None, None, None, ts, None, "null", 1)])
        records = _load_records(conn)
        r = records[0]
        assert r["account_from"] == ""
        assert r["account_to"] == ""
        assert r["note"] == ""
        assert r["category"] == ""

    def test_unknown_type_skipped(self):
        conn = sqlite3.connect(":memory:")
        ts = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp())
        _make_db(conn, [
            (1, 0, 10.0, "A", None, None, ts, None, "null", 1),  # expense
            (2, 99, 20.0, "B", None, None, ts, None, "null", 1),  # unknown
        ])
        records = _load_records(conn)
        assert len(records) == 1
        assert records[0]["id"] == "1"

    def test_all_four_types(self):
        conn = sqlite3.connect(":memory:")
        ts = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp())
        _make_db(conn, [
            (1, 0, 10.0, "A", None, None, ts, None, "null", 1),
            (2, 1, 20.0, "A", None, None, ts, None, "null", 1),
            (3, 2, 30.0, "A", "B", None, ts, None, "null", 1),
            (4, 3, 40.0, "A", None, None, ts, None, "null", 1),
        ])
        records = _load_records(conn)
        types = [r["type"] for r in records]
        assert types == ["expense", "income", "transfer", "repayment"]

    def test_cny_amount_converted(self):
        conn = sqlite3.connect(":memory:")
        ts = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp())
        extra = _extra("CNY", 1000.0, None, 0.0, "USD", 139.0)
        _make_db(conn, [(1, 0, 1000.0, "Alipay", None, None, ts, None, extra, 1)])
        records = _load_records(conn)
        assert records[0]["amount"] == 139.0

    def test_inactive_bills_excluded(self):
        conn = sqlite3.connect(":memory:")
        ts = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp())
        _make_db(conn, [
            (1, 0, 10.0, "A", None, "active", ts, None, "null", 1),
            (2, 0, 20.0, "A", None, "deleted", ts, None, "null", 0),
        ])
        records = _load_records(conn)
        assert len(records) == 1
        assert records[0]["note"] == "active"


# ── _load_balances ────────────────────────────────────────────────────────────


class TestLoadBalances:
    def test_active_accounts_only(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE user_asset (name TEXT, money NUMBER, currency TEXT, status INTEGER)")
        conn.execute("INSERT INTO user_asset VALUES ('Chase', 5000, 'USD', 0)")
        conn.execute("INSERT INTO user_asset VALUES ('Closed', 0, 'USD', 2)")
        balances = _load_balances(conn)
        assert "Chase" in balances
        assert "Closed" not in balances

    def test_returns_balance_and_currency(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE user_asset (name TEXT, money NUMBER, currency TEXT, status INTEGER)")
        conn.execute("INSERT INTO user_asset VALUES ('Chase', 5000.50, 'USD', 0)")
        conn.execute("INSERT INTO user_asset VALUES ('Alipay', 70000, 'CNY', 0)")
        balances = _load_balances(conn)
        assert balances["Chase"] == (5000.50, "USD")
        assert balances["Alipay"] == (70000, "CNY")

    def test_null_currency_defaults_to_usd(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE user_asset (name TEXT, money NUMBER, currency TEXT, status INTEGER)")
        conn.execute("INSERT INTO user_asset VALUES ('Old', 100, NULL, 0)")
        balances = _load_balances(conn)
        assert balances["Old"] == (100, "USD")


# ── Retirement flag — ingest_qianji_transactions ──────────────────────────────


def _fresh_db() -> Path:
    tmp = Path(tempfile.mktemp(suffix=".db"))
    init_db(tmp)
    return tmp


class TestIsRetirementFlag:
    def _sample_records(self) -> list[dict]:
        return [
            {"date": "2026-01-28", "type": "income", "category": "Salary", "amount": 8000,
             "account_from": "", "note": ""},
            {"date": "2026-01-28", "type": "income", "category": "401K", "amount": 1600,
             "account_from": "", "note": ""},
            {"date": "2026-01-10", "type": "expense", "category": "Rent", "amount": 2000,
             "account_from": "", "note": ""},
        ]

    def test_default_config_matches_401k_income(self) -> None:
        """'401K' is the user's retirement income category — flag should be set."""
        db = _fresh_db()
        try:
            ingest_qianji_transactions(
                db,
                self._sample_records(),
                retirement_categories=["401K", "401k Match"],
            )
            conn = sqlite3.connect(db)
            rows = conn.execute(
                "SELECT category, type, is_retirement FROM qianji_transactions ORDER BY date, category"
            ).fetchall()
            conn.close()
            assert ("401K", "income", 1) in rows
            assert ("Salary", "income", 0) in rows
            assert ("Rent", "expense", 0) in rows
        finally:
            db.unlink(missing_ok=True)

    def test_retirement_expense_not_flagged(self) -> None:
        """Flag only applies to income type — an expense in the list is not retirement."""
        db = _fresh_db()
        try:
            records = [
                {"date": "2026-01-01", "type": "expense", "category": "401K", "amount": 100,
                 "account_from": "", "note": ""},
            ]
            ingest_qianji_transactions(db, records, retirement_categories=["401K"])
            conn = sqlite3.connect(db)
            flag = conn.execute("SELECT is_retirement FROM qianji_transactions").fetchone()[0]
            conn.close()
            assert flag == 0
        finally:
            db.unlink(missing_ok=True)

    def test_empty_retirement_list_flags_nothing(self) -> None:
        db = _fresh_db()
        try:
            ingest_qianji_transactions(db, self._sample_records(), retirement_categories=[])
            conn = sqlite3.connect(db)
            count = conn.execute(
                "SELECT COUNT(*) FROM qianji_transactions WHERE is_retirement = 1"
            ).fetchone()[0]
            conn.close()
            assert count == 0
        finally:
            db.unlink(missing_ok=True)

    def test_case_sensitive_match(self) -> None:
        """Category matching is exact (case-sensitive) — '401k' will not match '401K'."""
        db = _fresh_db()
        try:
            records = [
                {"date": "2026-01-01", "type": "income", "category": "401k",
                 "amount": 1000, "account_from": "", "note": ""},
            ]
            ingest_qianji_transactions(db, records, retirement_categories=["401K"])
            conn = sqlite3.connect(db)
            flag = conn.execute("SELECT is_retirement FROM qianji_transactions").fetchone()[0]
            conn.close()
            assert flag == 0
        finally:
            db.unlink(missing_ok=True)


class TestVQianjiTxnsExposesIsRetirement:
    def test_view_aliases_camelcase(self) -> None:
        db = _fresh_db()
        try:
            records = [
                {"date": "2026-01-01", "type": "income", "category": "401K", "amount": 500,
                 "account_from": "", "note": ""},
            ]
            ingest_qianji_transactions(db, records, retirement_categories=["401K"])
            conn = sqlite3.connect(db)
            row = conn.execute(
                "SELECT date, type, category, amount, isRetirement FROM v_qianji_txns"
            ).fetchone()
            conn.close()
            assert row == ("2026-01-01", "income", "401K", 500.0, 1)
        finally:
            db.unlink(missing_ok=True)
