"""Unit tests for qianji_db ingest — parse_qj_amount, parse_qj_target_amount, _load_records, _load_balances, ingest_qianji_transactions."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from etl.db import init_db
from etl.qianji import (
    ingest_qianji_transactions,
    parse_qj_amount,
    parse_qj_target_amount,
)
from etl.qianji.ingest import _load_balances, _load_records

# ── parse_qj_amount ───────────────────────────────────────────────────────────


def _extra(ss: str, sv: float, ts: str | None, tv: float, bs: str, bv: float) -> str:
    return json.dumps({"curr": {"ss": ss, "sv": sv, "ts": ts, "tv": tv, "bs": bs, "bv": bv}})


def _record(
    *,
    date_: str = "2026-01-01",
    type_: str = "income",
    category: str = "Salary",
    amount: float = 100.0,
    account_from: str = "",
    account_to: str = "",
    note: str = "",
) -> dict:
    return {
        "date": date_,
        "type": type_,
        "category": category,
        "amount": amount,
        "account_from": account_from,
        "account_to": account_to,
        "note": note,
    }


def _rows(db_path: Path, sql: str) -> list[tuple]:
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(sql).fetchall()


def _scalar(db_path: Path, sql: str) -> object:
    return _rows(db_path, sql)[0][0]


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

    # ── Unconverted-label data quirk ─────────────────────────────────────
    # Qianji sometimes labels ``bs`` as the base currency (USD) but never
    # actually runs the conversion, producing ``ss != bs but bv == sv``.
    # When the user supplies a live CNY rate and the source is CNY,
    # ``parse_qj_amount`` converts ``money`` (source CNY) to USD.

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

    # ── Historical-rate lookup (per-bill-date) ───────────────────────────
    # The live-rate fallback used to revalue every quirk bill every build
    # with today's rate, which caused the USD amount to drift from run to run
    # and surfaced as "ghost adds" in the publish receipt. The right rate for
    # a 2024 bill is the 2024 rate — look it up by bill_date in a dict of
    # historical rates (loaded from ``daily_close WHERE symbol='CNY=X'``).

    def test_unconverted_cny_uses_historical_rate_when_bill_date_provided(self):
        """With bill_date + historical_cny_rates, use that date's rate."""
        extra = _extra("CNY", 7000.0, None, 0.0, "USD", 7000.0)
        rates = {date(2024, 5, 18): 7.2345, date(2026, 4, 18): 6.8164}
        # Bill is on 2024-05-18 → uses 7.2345, NOT today's 6.8164
        assert parse_qj_amount(
            7000.0, extra, bill_date=date(2024, 5, 18), historical_cny_rates=rates,
        ) == pytest.approx(7000.0 / 7.2345, rel=1e-6)

    def test_unconverted_cny_stable_across_live_rate_changes(self):
        """A 2024 bill's USD amount must NOT drift when today's FX rate moves.

        Regression guard for the root CNY bug — tomorrow's run with a new
        live rate must still compute the same USD as today's run for a
        historical bill. This eliminates the need for a cross-run stable
        identity (source_id) in the reporting snapshot.
        """
        extra = _extra("CNY", 7000.0, None, 0.0, "USD", 7000.0)
        bill_date = date(2024, 5, 18)
        historical = {bill_date: 7.2345}

        # Today's live rate fluctuates; history is fixed.
        run1 = parse_qj_amount(7000.0, extra, cny_rate=6.8164,
                               bill_date=bill_date, historical_cny_rates=historical)
        run2 = parse_qj_amount(7000.0, extra, cny_rate=6.9003,
                               bill_date=bill_date, historical_cny_rates=historical)
        assert run1 == run2
        assert run1 == pytest.approx(7000.0 / 7.2345, rel=1e-6)

    def test_unconverted_cny_walks_back_to_last_weekday_rate(self):
        """Qianji bills are timestamped to wall-clock; yfinance only has
        weekday close rates. For a Saturday bill, fall back to Friday's rate.
        """
        extra = _extra("CNY", 1000.0, None, 0.0, "USD", 1000.0)
        friday = date(2024, 5, 17)  # 2024-05-18 is a Saturday
        saturday = date(2024, 5, 18)
        rates = {friday: 7.2345}  # only Friday present
        result = parse_qj_amount(
            1000.0, extra, bill_date=saturday, historical_cny_rates=rates,
        )
        assert result == pytest.approx(1000.0 / 7.2345, rel=1e-6)

    def test_unconverted_cny_falls_back_to_scalar_when_no_historical_match(self):
        """If historical_cny_rates has no rate near bill_date, use scalar fallback."""
        extra = _extra("CNY", 5000.0, None, 0.0, "USD", 5000.0)
        # Empty historical dict + bill_date → scalar rate used
        result = parse_qj_amount(
            5000.0, extra, cny_rate=7.0,
            bill_date=date(2024, 5, 18), historical_cny_rates={},
        )
        assert result == pytest.approx(5000.0 / 7.0, rel=1e-6)

    def test_unconverted_cny_no_bill_date_preserves_scalar_path(self):
        """Legacy calls without bill_date still use scalar rate (backcompat)."""
        extra = _extra("CNY", 5000.0, None, 0.0, "USD", 5000.0)
        assert parse_qj_amount(5000.0, extra, cny_rate=7.0) == pytest.approx(
            5000.0 / 7.0, rel=1e-6,
        )


# ── parse_qj_target_amount ────────────────────────────────────────────────────


class TestParseQjTargetAmount:
    """Target-currency parser: returns tv for cross-currency transfers, else money."""

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
        assert records[0]["id"] == "1"

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
        """`_load_records` must thread cny_rate into parse_qj_amount so the
        unconverted-label data quirk (ss=CNY bs=USD bv==sv) gets converted.
        Without this hook, cross-currency expenses in that shape bypass the
        rate entirely and inflate the USD cashflow figure by ~7×.
        """
        extra = _extra("CNY", 5000.0, None, 0.0, "USD", 5000.0)  # quirk
        bills = [_bill(1, money=5000.0, fromact="Alipay", extra=extra)]
        # Without rate: warn + fall back to money (5000).
        assert _records_for(bills)[0]["amount"] == 5000.0
        # With rate: convert 5000 CNY / 7.0 ≈ 714.29.
        assert _records_for(bills, cny_rate=7.0)[0]["amount"] == pytest.approx(714.2857, rel=1e-3)

    def test_balance_adjustment_rows_skipped(self):
        """Manual reconciliation rows (remark 'Balance adjustment(X ~ Y)'
        or short 'adjust') are not real cashflow — they should be dropped
        at ingest so expense/income aggregates aren't inflated.
        """
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
        # 2026-04-10 06:30 UTC == 2026-04-09 23:30 PT (PDT, UTC-7)
        ts = int(datetime(2026, 4, 10, 6, 30, tzinfo=UTC).timestamp())
        records = _records_for([_bill(1, money=15.0, remark="late-night snack", ts=ts)])
        # In PT this is 2026-04-09, not 2026-04-10 (as UTC would say)
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
    def test_active_accounts_only(self):
        balances = _balances_for([("Chase", 5000, "USD", 0), ("Closed", 0, "USD", 2)])
        assert "Chase" in balances
        assert "Closed" not in balances

    def test_returns_balance_and_currency(self):
        balances = _balances_for([("Chase", 5000.50, "USD", 0), ("Alipay", 70000, "CNY", 0)])
        assert balances["Chase"] == (5000.50, "USD")
        assert balances["Alipay"] == (70000, "CNY")

    def test_null_currency_defaults_to_usd(self):
        balances = _balances_for([("Old", 100, None, 0)])
        assert balances["Old"] == (100, "USD")


# ── ingest_qianji_transactions — DB writes ────────────────────────────────────


def _fresh_db() -> Path:
    tmp = Path(tempfile.mktemp(suffix=".db"))
    init_db(tmp)
    return tmp


class TestIngestQianjiTransactions:
    @pytest.fixture()
    def db_path(self, empty_db: Path) -> Path:
        return empty_db

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


class TestAccountToNormalization:
    """The ingest layer stores a *semantic* destination account in the
    ``account_to`` column: the account that received money. For transfers
    that's ``targetact`` (QianjiRecord.account_to). For income, Qianji
    stores the receiving account in ``fromact`` (see etl/qianji/balances:
    type=1 does ``balances[fromact] += money``), so we normalize to surface
    that as the destination too — the frontend's Fidelity cross-check shouldn't
    have to know Qianji's per-type direction quirk.
    """

    def test_transfer_uses_account_to(self) -> None:
        db = _fresh_db()
        try:
            records = [
                {"date": "2026-01-01", "type": "transfer", "category": "", "amount": 1000,
                 "account_from": "Chase Debit", "account_to": "Fidelity taxable", "note": ""},
            ]
            ingest_qianji_transactions(db, records)
            conn = sqlite3.connect(db)
            row = conn.execute(
                "SELECT type, account_to FROM qianji_transactions"
            ).fetchone()
            conn.close()
            assert row == ("transfer", "Fidelity taxable")
        finally:
            db.unlink(missing_ok=True)

    def test_income_uses_account_from_as_destination(self) -> None:
        """Qianji stores direct-deposit income's receiving account in fromact."""
        db = _fresh_db()
        try:
            records = [
                {"date": "2026-01-01", "type": "income", "category": "Salary", "amount": 3000,
                 "account_from": "Fidelity taxable", "account_to": "", "note": ""},
            ]
            ingest_qianji_transactions(db, records)
            conn = sqlite3.connect(db)
            row = conn.execute(
                "SELECT type, account_to FROM qianji_transactions"
            ).fetchone()
            conn.close()
            assert row == ("income", "Fidelity taxable")
        finally:
            db.unlink(missing_ok=True)

    def test_expense_account_to_defaults_to_empty(self) -> None:
        """Expenses have no destination — column stays empty."""
        db = _fresh_db()
        try:
            records = [
                {"date": "2026-01-01", "type": "expense", "category": "Rent", "amount": 2000,
                 "account_from": "Chase Debit", "account_to": "", "note": ""},
            ]
            ingest_qianji_transactions(db, records)
            conn = sqlite3.connect(db)
            row = conn.execute(
                "SELECT type, account_to FROM qianji_transactions"
            ).fetchone()
            conn.close()
            assert row == ("expense", "")
        finally:
            db.unlink(missing_ok=True)
