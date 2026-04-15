"""Unit tests for qianji_db ingest — parse_qj_amount, parse_qj_target_amount, _load_records, _load_balances, ingest_qianji_transactions."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from etl.db import init_db
from etl.ingest.qianji_db import (
    _load_balances,
    _load_records,
    ingest_qianji_transactions,
    parse_qj_amount,
    parse_qj_target_amount,
)

# ── parse_qj_amount ───────────────────────────────────────────────────────────


def _extra(ss: str, sv: float, ts: str | None, tv: float, bs: str, bv: float) -> str:
    return json.dumps({"curr": {"ss": ss, "sv": sv, "ts": ts, "tv": tv, "bs": bs, "bv": bv}})


class TestParseQjAmount:
    def test_null_string_returns_money(self):
        assert parse_qj_amount(100.0, "null") == 100.0

    def test_none_returns_money(self):
        assert parse_qj_amount(100.0, None) == 100.0

    def test_cny_expense_uses_bv(self):
        """CNY expense with ts=None: money=2590.52 (CNY), bv=358.0 (USD)."""
        extra = _extra("CNY", 2590.52, None, 0.0, "USD", 358.0)
        assert parse_qj_amount(2590.52, extra) == 358.0

    def test_cny_to_usd_transfer_uses_bv(self):
        """CNY→USD transfer: money=5000 (CNY), bv=692 (USD)."""
        extra = _extra("CNY", 5000.0, "USD", 692.0, "USD", 692.0)
        assert parse_qj_amount(5000.0, extra) == 692.0

    def test_cny_to_cny_transfer_bv_converted(self):
        """CNY→CNY transfer where bv has USD conversion (bv != sv)."""
        extra = _extra("CNY", 10000.0, "CNY", 10000.0, "USD", 1385.0)
        assert parse_qj_amount(10000.0, extra) == 1385.0

    def test_cny_to_cny_transfer_bv_equals_sv(self):
        """CNY→CNY transfer where bv == sv (no conversion happened).

        Should fall back to money since bv == sv means no real conversion.
        """
        extra = _extra("CNY", 7000.0, "CNY", 7000.0, "USD", 7000.0)
        # bv == sv → no conversion detected → fallback to money
        assert parse_qj_amount(7000.0, extra) == 7000.0

    def test_usd_source_falls_back_to_money(self):
        """USD→CNY transfer: ss=USD, code should use money directly."""
        extra = _extra("USD", 2000.0, "CNY", 14366.0, "USD", 2000.0)
        assert parse_qj_amount(2000.0, extra) == 2000.0

    def test_malformed_json_returns_money(self):
        assert parse_qj_amount(50.0, "not json") == 50.0

    def test_extra_without_curr_returns_money(self):
        assert parse_qj_amount(50.0, json.dumps({"tags": None})) == 50.0

    def test_bv_none_returns_money(self):
        extra = json.dumps({"curr": {"ss": "CNY", "sv": 100.0, "bs": "USD", "bv": None}})
        assert parse_qj_amount(100.0, extra) == 100.0

    def test_curr_not_dict_returns_money(self):
        """curr present but not a dict (e.g. int, list)."""
        assert parse_qj_amount(50.0, json.dumps({"curr": 123})) == 50.0
        assert parse_qj_amount(50.0, json.dumps({"curr": ["CNY"]})) == 50.0

    def test_curr_missing_fields_returns_money(self):
        """curr dict but missing required fields."""
        assert parse_qj_amount(50.0, json.dumps({"curr": {"ss": "CNY"}})) == 50.0
        assert parse_qj_amount(50.0, json.dumps({"curr": {"ss": "CNY", "bs": "USD"}})) == 50.0
        assert parse_qj_amount(50.0, json.dumps({"curr": {"ss": "CNY", "bs": "USD", "bv": 7.0}})) == 50.0

    def test_ss_equals_bs_returns_money(self):
        """Same source and base currency — no conversion needed."""
        extra = _extra("USD", 100.0, None, 0.0, "USD", 100.0)
        assert parse_qj_amount(100.0, extra) == 100.0

    def test_bv_sv_within_tolerance_returns_money(self):
        """bv and sv differ by less than tolerance — treat as unconverted."""
        extra = _extra("CNY", 100.0, None, 0.0, "USD", 100.005)
        assert parse_qj_amount(100.0, extra) == 100.0

    def test_empty_string_returns_money(self):
        assert parse_qj_amount(50.0, "") == 50.0

    # ── Unconverted-label data quirk ─────────────────────────────────────
    # Qianji sometimes labels ``bs`` as the base currency (USD) but never
    # actually runs the conversion, producing ``ss != bs but bv == sv``.
    # When the user supplies a live CNY rate and the source is CNY,
    # ``parse_qj_amount`` converts ``money`` (source CNY) to USD.

    def test_unconverted_cny_to_usd_with_rate_converts(self):
        """ss=CNY bs=USD bv==sv=5000 — with rate, convert money/rate."""
        extra = _extra("CNY", 5000.0, None, 0.0, "USD", 5000.0)
        # 5000 CNY / 7.0 ≈ 714.2857
        assert parse_qj_amount(5000.0, extra, cny_rate=7.0) == pytest.approx(714.2857, rel=1e-3)

    def test_unconverted_cny_to_usd_without_rate_returns_money(self):
        """Same quirk but no rate → logs warning, falls back to money unchanged."""
        extra = _extra("CNY", 5000.0, None, 0.0, "USD", 5000.0)
        # No rate → stays at 5000 (the old, wrong-but-safe behavior)
        assert parse_qj_amount(5000.0, extra) == 5000.0

    def test_unconverted_non_cny_quirk_with_rate_returns_money(self):
        """Rate is CNY-specific — EUR→USD quirk with rate still falls back."""
        extra = _extra("EUR", 100.0, None, 0.0, "USD", 100.0)
        assert parse_qj_amount(100.0, extra, cny_rate=7.0) == 100.0

    def test_unconverted_quirk_zero_rate_returns_money(self):
        """cny_rate=0 is falsy → skip conversion (division would blow up)."""
        extra = _extra("CNY", 5000.0, None, 0.0, "USD", 5000.0)
        assert parse_qj_amount(5000.0, extra, cny_rate=0.0) == 5000.0


# ── parse_qj_target_amount ────────────────────────────────────────────────────


class TestParseQjTargetAmount:
    """Target-currency parser: returns tv for cross-currency transfers, else money."""

    def test_no_extra(self) -> None:
        assert parse_qj_target_amount(100.0, None) == 100.0
        assert parse_qj_target_amount(100.0, "null") == 100.0

    def test_invalid_json(self) -> None:
        assert parse_qj_target_amount(100.0, "not json") == 100.0

    def test_same_currency(self) -> None:
        """Same source/target currency returns original money."""
        extra = '{"curr": {"ss": "USD", "ts": "USD", "tv": 100}}'
        assert parse_qj_target_amount(100.0, extra) == 100.0

    def test_cross_currency(self) -> None:
        """Cross-currency returns tv from extra."""
        extra = '{"curr": {"ss": "USD", "ts": "CNY", "tv": 723.5}}'
        assert parse_qj_target_amount(100.0, extra) == 723.5

    def test_missing_curr_key(self) -> None:
        extra = '{"other": "data"}'
        assert parse_qj_target_amount(100.0, extra) == 100.0

    def test_tv_zero_returns_money(self) -> None:
        """tv <= 0 should fall back to money."""
        extra = '{"curr": {"ss": "USD", "ts": "CNY", "tv": 0}}'
        assert parse_qj_target_amount(100.0, extra) == 100.0


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

    def test_cny_rate_passed_through_to_parse_qj_amount(self):
        """`_load_records` must thread cny_rate into parse_qj_amount so the
        unconverted-label data quirk (ss=CNY bs=USD bv==sv) gets converted.
        Without this hook, cross-currency expenses in that shape bypass the
        rate entirely and inflate the USD cashflow figure by ~7×.
        """
        conn = sqlite3.connect(":memory:")
        ts = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp())
        extra = _extra("CNY", 5000.0, None, 0.0, "USD", 5000.0)  # quirk
        _make_db(conn, [(1, 0, 5000.0, "Alipay", None, None, ts, None, extra, 1)])
        # Without rate: warn + fall back to money (5000).
        assert _load_records(conn)[0]["amount"] == 5000.0
        # With rate: convert 5000 CNY / 7.0 ≈ 714.29.
        assert _load_records(conn, cny_rate=7.0)[0]["amount"] == pytest.approx(714.2857, rel=1e-3)

    def test_balance_adjustment_rows_skipped(self):
        """Manual reconciliation rows (remark 'Balance adjustment(X ~ Y)'
        or short 'adjust') are not real cashflow — they should be dropped
        at ingest so expense/income aggregates aren't inflated.
        """
        conn = sqlite3.connect(":memory:")
        ts = int(datetime(2025, 1, 15, 12, 0, tzinfo=UTC).timestamp())
        _make_db(conn, [
            (1, 0, 10.0, "A", None, "groceries", ts, None, "null", 1),
            (2, 0, 500.0, "A", None, "Balance adjustment(29,338.34 ~ 25,524.00)", ts, None, "null", 1),
            (3, 0, 6.0, "A", None, "adjust", ts, None, "null", 1),
            (4, 0, 7.0, "A", None, "ADJUST", ts, None, "null", 1),         # case-insensitive
            (5, 0, 8.0, "A", None, "  balance adjustment (y~x)", ts, None, "null", 1),  # leading space
            (6, 0, 9.0, "A", None, "maladjusted dinner", ts, None, "null", 1),  # word-boundary: NOT a match
        ])
        records = _load_records(conn)
        notes = [r["note"] for r in records]
        assert "groceries" in notes
        assert "maladjusted dinner" in notes  # substring "adjust" but not a prefix → kept
        # All balance-adjustment forms filtered out
        assert not any("adjustment" in n.lower() or n.lower().strip() == "adjust" for n in notes)
        assert len(records) == 2

    def test_date_truncation_uses_user_timezone(self):
        """Bills are attributed to the user's wall-clock day, not UTC.

        A bill logged at 23:30 PT on 2026-04-09 has Unix ts corresponding
        to 06:30 UTC on 2026-04-10 — in UTC it'd roll to the next day,
        in PT it stays on the 9th. The pipeline must pick PT so daily
        cashflow matches the user's experience.
        """
        conn = sqlite3.connect(":memory:")
        # 2026-04-10 06:30 UTC == 2026-04-09 23:30 PT (PDT, UTC-7)
        ts = int(datetime(2026, 4, 10, 6, 30, tzinfo=UTC).timestamp())
        _make_db(conn, [(1, 0, 15.0, "A", None, "late-night snack", ts, None, "null", 1)])
        records = _load_records(conn)
        # In PT this is 2026-04-09, not 2026-04-10 (as UTC would say)
        assert records[0]["date"].startswith("2026-04-09")


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


# ── ingest_qianji_transactions — DB writes ────────────────────────────────────


def _fresh_db() -> Path:
    tmp = Path(tempfile.mktemp(suffix=".db"))
    init_db(tmp)
    return tmp


class TestIngestQianjiTransactions:
    @pytest.fixture()
    def db_path(self, tmp_path: Path) -> Path:
        p = tmp_path / "test.db"
        init_db(p)
        return p

    def test_ingest_records(self, db_path: Path) -> None:
        records = [
            {"date": "2025-03-01", "type": "income", "category": "Salary", "amount": 5000.0, "account_from": "Checking", "note": ""},
            {"date": "2025-03-05", "type": "expense", "category": "Rent", "amount": 1500.0, "account_from": "Checking", "note": ""},
        ]
        count = ingest_qianji_transactions(db_path, records)
        assert count == 2

    def test_clears_and_replaces(self, db_path: Path) -> None:
        records = [{"date": "2025-03-01", "type": "income", "category": "Salary", "amount": 5000.0, "account_from": "Checking", "note": ""}]
        ingest_qianji_transactions(db_path, records)
        new_records = [{"date": "2025-04-01", "type": "expense", "category": "Food", "amount": 100.0, "account_from": "Checking", "note": ""}]
        count = ingest_qianji_transactions(db_path, new_records)
        assert count == 1  # old rows cleared

    def test_empty_records(self, db_path: Path) -> None:
        count = ingest_qianji_transactions(db_path, [])
        assert count == 0


# ── Retirement flag — ingest_qianji_transactions ──────────────────────────────


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
