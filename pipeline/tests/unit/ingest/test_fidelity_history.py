"""Tests for Fidelity Accounts History CSV parser and DB ingestion.

After the data-source abstraction refactor (Phase 3 — Task 16), the parsing
and DB-ingest code live in :mod:`etl.sources.fidelity`. The tests below call
:meth:`FidelitySource._ingest_one_csv` for per-file ingestion and import
``load_transactions`` from the new module.
"""

import sqlite3
from pathlib import Path

import pytest

from etl.db import init_db
from etl.parsing import parse_us_date
from etl.sources.fidelity import _ingest_one_csv, load_transactions


def ingest_fidelity_csv(db_path: Path, csv_path: Path) -> int:
    """Back-compat shim: :func:`etl.sources.fidelity._ingest_one_csv` under the
    legacy name. Keeps this test file's call sites unchanged post-refactor.
    """
    return _ingest_one_csv(db_path, csv_path)


class TestLoadTransactions:
    """Tests for load_transactions() using tests/fixtures/history_sample.csv."""

    @pytest.fixture()
    def transactions(self, history_sample_csv: Path) -> list[dict]:
        return load_transactions(history_sample_csv)

    # -- record count --

    def test_correct_total_record_count(self, transactions: list[dict]) -> None:
        """The fixture has 16 data rows (after 2 blank lines + header)."""
        assert len(transactions) == 16

    # -- required keys --

    def test_all_records_have_required_keys(self, transactions: list[dict]) -> None:
        required = {
            "date",
            "account",
            "action_type",
            "symbol",
            "description",
            "quantity",
            "price",
            "amount",
            "raw_action",
            "dedup_key",
        }
        for txn in transactions:
            assert required.issubset(txn.keys()), f"Missing keys in {txn}"

    # -- blank-line skipping --

    def test_skips_blank_lines_before_header(self, transactions: list[dict]) -> None:
        """The CSV has 2 blank lines before the header; parser should not choke."""
        assert len(transactions) > 0
        # First record should be the 04/02/2026 EFT deposit, normalized to ISO
        assert transactions[0]["date"] == "2026-04-02"

    # -- date normalization --

    def test_all_dates_are_iso(self, transactions: list[dict]) -> None:
        """Run dates are normalized from MM/DD/YYYY to ISO YYYY-MM-DD at ingestion."""
        import re
        iso_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        for txn in transactions:
            assert iso_re.match(txn["date"]), f"Non-ISO date: {txn['date']!r}"

    # -- deposit --

    def test_parses_deposit(self, transactions: list[dict]) -> None:
        deposits = [t for t in transactions if t["action_type"] == "deposit"]
        assert len(deposits) == 2
        # First deposit is $1500
        assert deposits[0]["amount"] == pytest.approx(1500.0)
        assert deposits[0]["account"] == "Z29133576"
        assert "Electronic Funds Transfer" in deposits[0]["raw_action"]

    # -- buy --

    def test_parses_buy(self, transactions: list[dict]) -> None:
        buys = [t for t in transactions if t["action_type"] == "buy"]
        assert len(buys) == 3  # AAPL, GLDM, SVIX
        aapl_buy = next(t for t in buys if t["symbol"] == "AAPL")
        assert aapl_buy["quantity"] == pytest.approx(3.0)
        assert aapl_buy["price"] == pytest.approx(252.56)
        assert aapl_buy["amount"] == pytest.approx(-757.68)

    # -- sell --

    def test_parses_sell(self, transactions: list[dict]) -> None:
        sells = [t for t in transactions if t["action_type"] == "sell"]
        assert len(sells) == 2  # SVIX and VTEB
        svix_sell = next(t for t in sells if t["symbol"] == "SVIX")
        assert svix_sell["quantity"] == pytest.approx(-20.0)
        assert svix_sell["amount"] == pytest.approx(341.2)

    # -- dividend and reinvestment --

    def test_parses_dividend(self, transactions: list[dict]) -> None:
        divs = [t for t in transactions if t["action_type"] == "dividend"]
        assert len(divs) == 2  # NVDA and QQQM
        nvda_div = next(t for t in divs if t["symbol"] == "NVDA")
        assert nvda_div["amount"] == pytest.approx(0.2)

    def test_parses_reinvestment(self, transactions: list[dict]) -> None:
        reins = [t for t in transactions if t["action_type"] == "reinvestment"]
        assert len(reins) == 2  # NVDA and QQQM
        qqqm_rein = next(t for t in reins if t["symbol"] == "QQQM")
        assert qqqm_rein["amount"] == pytest.approx(-0.98)
        assert qqqm_rein["price"] == pytest.approx(238.69)

    # -- lending and collateral --

    def test_classifies_lending(self, transactions: list[dict]) -> None:
        lending = [t for t in transactions if t["action_type"] == "lending"]
        assert len(lending) == 2  # Two LOAN RETURNED rows
        for txn in lending:
            assert "LOAN RETURNED" in txn["raw_action"]

    def test_classifies_collateral(self, transactions: list[dict]) -> None:
        collateral = [t for t in transactions if t["action_type"] == "collateral"]
        assert len(collateral) == 1
        assert "DECREASE COLLATERAL" in collateral[0]["raw_action"]

    # -- interest --

    def test_parses_interest(self, transactions: list[dict]) -> None:
        interest = [t for t in transactions if t["action_type"] == "interest"]
        assert len(interest) == 1
        assert interest[0]["amount"] == pytest.approx(1.02)
        assert interest[0]["symbol"] == "123990BL6"

    # -- foreign tax --

    def test_parses_foreign_tax(self, transactions: list[dict]) -> None:
        ftax = [t for t in transactions if t["action_type"] == "foreign_tax"]
        assert len(ftax) == 1
        assert ftax[0]["amount"] == pytest.approx(-5.67)
        assert ftax[0]["symbol"] == "TSM"

    # -- dedup_key --

    def test_dedup_key_is_tuple(self, transactions: list[dict]) -> None:
        for txn in transactions:
            assert isinstance(txn["dedup_key"], tuple), f"dedup_key should be tuple, got {type(txn['dedup_key'])}"

    def test_dedup_key_structure(self, transactions: list[dict]) -> None:
        """dedup_key is (date, account_number, raw_action, symbol, amount)."""
        txn = transactions[0]  # First EFT deposit
        key = txn["dedup_key"]
        assert len(key) == 5
        assert key == (txn["date"], txn["account"], txn["raw_action"], txn["symbol"], txn["amount"])

    def test_dedup_keys_are_unique(self, transactions: list[dict]) -> None:
        keys = [t["dedup_key"] for t in transactions]
        assert len(keys) == len(set(keys)), "Duplicate dedup_keys found"

    # -- edge: account field uses account number, not display name --

    def test_account_uses_number_not_name(self, transactions: list[dict]) -> None:
        roth_txns = [t for t in transactions if t["account"] == "238986483"]
        assert len(roth_txns) == 2  # SVIX buy and sell in ROTH IRA


class TestFidelityDateParse:
    """Tests for Fidelity's strict MM/DD/YYYY → ISO conversion via parse_us_date."""

    def test_happy_path(self) -> None:
        assert parse_us_date("01/15/2026", strict=True) == "2026-01-15"

    def test_preserves_leading_zeros(self) -> None:
        assert parse_us_date("09/04/2026", strict=True) == "2026-09-04"

    def test_end_of_year(self) -> None:
        assert parse_us_date("12/31/2025", strict=True) == "2025-12-31"

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            parse_us_date("", strict=True)

    def test_rejects_one_digit_month(self) -> None:
        """Fidelity exports use zero-padded months; reject single digits."""
        with pytest.raises(ValueError, match="Invalid"):
            parse_us_date("1/15/2026", strict=True)

    def test_rejects_iso_date(self) -> None:
        """ISO format must be rejected at the Fidelity boundary."""
        with pytest.raises(ValueError, match="Invalid"):
            parse_us_date("2026-01-15", strict=True)

    def test_rejects_garbage(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            parse_us_date("abc", strict=True)

    def test_error_message_includes_row_context(self) -> None:
        with pytest.raises(ValueError, match=r"Accounts_History\.csv row 42"):
            parse_us_date("bad", strict=True, row_context="Accounts_History.csv row 42")


class TestIngestFidelity:
    """Tests for ingest_fidelity_csv — CSV → timemachine.db row writes."""

    @pytest.fixture()
    def db_path(self, tmp_path: Path) -> Path:
        p = tmp_path / "test.db"
        init_db(p)
        return p

    def test_ingest_sample_csv(self, db_path: Path, history_sample_csv: Path) -> None:
        count = ingest_fidelity_csv(db_path, history_sample_csv)
        assert count > 0
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM fidelity_transactions").fetchone()[0]
        conn.close()
        assert rows == count

    def test_overlap_replaces(self, db_path: Path, history_sample_csv: Path) -> None:
        ingest_fidelity_csv(db_path, history_sample_csv)
        count2 = ingest_fidelity_csv(db_path, history_sample_csv)
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM fidelity_transactions").fetchone()[0]
        conn.close()
        assert rows == count2  # replaced, not doubled

    def test_run_dates_normalized_to_iso(self, db_path: Path, history_sample_csv: Path) -> None:
        """Run dates must be stored as ISO YYYY-MM-DD, not raw MM/DD/YYYY."""
        import re
        ingest_fidelity_csv(db_path, history_sample_csv)
        conn = sqlite3.connect(str(db_path))
        run_dates = [r[0] for r in conn.execute("SELECT run_date FROM fidelity_transactions")]
        conn.close()
        assert run_dates  # non-empty sanity check
        iso_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        for rd in run_dates:
            assert iso_re.match(rd), f"Non-ISO run_date in DB: {rd!r}"


# Header shared by every synthetic CSV below — same shape as the real Fidelity
# export (2 blank lines + 18-column header).
_FIDELITY_HEADER = (
    "\n\n"
    "Run Date,Account,Account Number,Action,Symbol,Description,Type,"
    "Exchange Quantity,Exchange Currency,Currency,Price,Quantity,"
    "Exchange Rate,Commission,Fees,Accrued Interest,Amount,Settlement Date\n"
)


def _write_csv(path: Path, rows: list[str]) -> Path:
    path.write_text(_FIDELITY_HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    return path


# Canonical rows — line shape mirrors history_sample.csv so _parse_csv/_classify_action
# behave identically to a real export. Mutate only the fields the test cares about.
_ROW_AAPL = (
    '04/02/2026,"Taxable","Z29133576","YOU BOUGHT APPLE INC (AAPL) (Cash)",AAPL,'
    '"APPLE INC",Cash,0,,USD,252.56,3,0,,,,-757.68,04/06/2026'
)
_ROW_GLDM = (
    '04/02/2026,"Taxable","Z29133576","YOU BOUGHT WORLD GOLD TR SPDR GLD MINIS (GLDM) (Cash)",GLDM,'
    '"WORLD GOLD TR SPDR GLD MINIS",Cash,0,,USD,91.13,10,0,,,,-911.3,04/06/2026'
)
_ROW_EFT = (
    '04/02/2026,"Taxable","Z29133576","Electronic Funds Transfer Received (Cash)", ,'
    '"No Description",Cash,0,,USD,,0.000,0,,,,1500,'
)


class TestIngestFidelityRangeReplace:
    """Range-replace semantics for ingest_fidelity_csv.

    Each Fidelity CSV export is an authoritative snapshot of its date range.
    Re-ingesting a CSV replaces any existing rows in that range with the new
    CSV's rows — no row-level dedup, because intra-day duplicate trades are
    legitimate and CSV alone cannot distinguish them from literal duplicates.
    Share-count correctness is verified out-of-band via
    ``scripts/verify_positions.py`` against Fidelity's Portfolio_Positions CSV.
    """

    @pytest.fixture()
    def db_path(self, tmp_path: Path) -> Path:
        p = tmp_path / "test.db"
        init_db(p)
        return p

    def test_same_csv_ingest_is_idempotent(self, db_path: Path, history_sample_csv: Path) -> None:
        """Re-ingesting the same CSV produces the same row set — DELETE wipes the range, INSERT rebuilds it."""
        count1 = ingest_fidelity_csv(db_path, history_sample_csv)
        count2 = ingest_fidelity_csv(db_path, history_sample_csv)
        assert count1 == count2
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM fidelity_transactions").fetchone()[0]
        conn.close()
        assert rows == count1

    def test_subset_csv_replaces_range(self, db_path: Path, tmp_path: Path) -> None:
        """A newer CSV covering the same date range supersedes the older CSV within that range.

        Fidelity's CSV export is authoritative for its range. If a re-export drops a row,
        the current truth is "that row no longer exists in Fidelity's records" — the DB
        must reflect this, not cling to stale data. Share-count divergence would be caught
        by verify_positions.py against the latest Portfolio_Positions snapshot.
        """
        full_csv = _write_csv(tmp_path / "full.csv", [_ROW_AAPL, _ROW_GLDM, _ROW_EFT])
        subset_csv = _write_csv(tmp_path / "subset.csv", [_ROW_AAPL, _ROW_GLDM])

        ingest_fidelity_csv(db_path, full_csv)
        ingest_fidelity_csv(db_path, subset_csv)

        conn = sqlite3.connect(str(db_path))
        count = conn.execute("SELECT COUNT(*) FROM fidelity_transactions").fetchone()[0]
        symbols = {r[0] for r in conn.execute("SELECT symbol FROM fidelity_transactions")}
        conn.close()

        assert count == 2, "subset CSV is the latest authoritative snapshot for its range"
        assert symbols == {"AAPL", "GLDM"}

    def test_intra_day_duplicate_trades_preserved(self, db_path: Path, tmp_path: Path) -> None:
        """Two identical CSV rows (same date/action/symbol/qty/price/amount) represent two
        real trades and must both be ingested. Row-level dedup would silently erase one."""
        csv = _write_csv(tmp_path / "dup.csv", [_ROW_AAPL, _ROW_AAPL])

        ingest_fidelity_csv(db_path, csv)

        conn = sqlite3.connect(str(db_path))
        aapl_rows = conn.execute(
            "SELECT COUNT(*) FROM fidelity_transactions WHERE symbol='AAPL'"
        ).fetchone()[0]
        conn.close()
        assert aapl_rows == 2, "intra-day duplicate trades must both be stored"

    def test_different_date_csvs_coexist(self, db_path: Path, tmp_path: Path) -> None:
        """Two CSVs covering disjoint date ranges both populate the DB — DELETE BETWEEN is
        bounded by each CSV's own min/max date."""
        row_apr = _ROW_AAPL  # 04/02/2026
        row_may = (
            '05/02/2026,"Taxable","Z29133576","YOU BOUGHT APPLE INC (AAPL) (Cash)",AAPL,'
            '"APPLE INC",Cash,0,,USD,260.00,2,0,,,,-520.00,05/06/2026'
        )
        csv_apr = _write_csv(tmp_path / "apr.csv", [row_apr])
        csv_may = _write_csv(tmp_path / "may.csv", [row_may])

        ingest_fidelity_csv(db_path, csv_apr)
        ingest_fidelity_csv(db_path, csv_may)

        conn = sqlite3.connect(str(db_path))
        dates = sorted(r[0] for r in conn.execute("SELECT run_date FROM fidelity_transactions"))
        conn.close()
        assert dates == ["2026-04-02", "2026-05-02"], "disjoint CSVs both survive"

    def test_init_db_is_idempotent(self, tmp_path: Path) -> None:
        """init_db is safe to call on an existing DB — all CREATE statements use IF NOT EXISTS."""
        db_path = tmp_path / "clean.db"
        init_db(db_path)
        init_db(db_path)
        init_db(db_path)

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM fidelity_transactions").fetchone()[0]
        conn.close()
        assert rows == 0
