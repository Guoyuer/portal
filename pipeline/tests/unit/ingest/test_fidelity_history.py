"""Tests for Fidelity Accounts History CSV parser and DB ingestion."""

import sqlite3
from pathlib import Path

import pytest

from generate_asset_snapshot.db import init_db
from generate_asset_snapshot.ingest.fidelity_history import ingest_fidelity_csv, load_transactions
from generate_asset_snapshot.parsing import parse_us_date


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


class TestIngestFidelityNaturalKey:
    """Natural-key dedup invariants for ingest_fidelity_csv.

    Replaces the old range-replace semantics (DELETE WHERE run_date BETWEEN ...; INSERT)
    which could silently drop rows when a newer CSV was a date-range subset of an older one.
    Natural key = (run_date, action, symbol, quantity, price, amount).
    """

    @pytest.fixture()
    def db_path(self, tmp_path: Path) -> Path:
        p = tmp_path / "test.db"
        init_db(p)
        return p

    def test_subset_csv_preserves_missing_rows(self, db_path: Path, tmp_path: Path) -> None:
        """If a later CSV is a subset of an earlier one (Fidelity drops a row), the
        missing row must be preserved — never silently wiped by a range DELETE."""
        full_csv = _write_csv(tmp_path / "full.csv", [_ROW_AAPL, _ROW_GLDM, _ROW_EFT])
        subset_csv = _write_csv(tmp_path / "subset.csv", [_ROW_AAPL, _ROW_GLDM])  # missing EFT

        count1 = ingest_fidelity_csv(db_path, full_csv)
        assert count1 == 3

        count2 = ingest_fidelity_csv(db_path, subset_csv)

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM fidelity_transactions").fetchone()[0]
        symbols = {r[0] for r in conn.execute("SELECT symbol FROM fidelity_transactions")}
        conn.close()

        assert rows == 3, "subset ingest must not delete rows missing from the new CSV"
        assert count2 == 3
        assert symbols == {"AAPL", "GLDM", ""}, "the EFT row (empty symbol) must still be present"

    def test_duplicate_ingest_is_noop(self, db_path: Path, history_sample_csv: Path) -> None:
        """Ingesting the same CSV twice must not create duplicate rows and must not error."""
        count1 = ingest_fidelity_csv(db_path, history_sample_csv)
        count2 = ingest_fidelity_csv(db_path, history_sample_csv)
        assert count1 == count2
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM fidelity_transactions").fetchone()[0]
        conn.close()
        assert rows == count1

    def test_different_quantity_is_separate_row(self, db_path: Path, tmp_path: Path) -> None:
        """Two rows with the same date/action/symbol/price/amount but different quantity
        are different natural keys → both preserved."""
        row_qty10 = (
            '04/02/2026,"Taxable","Z29133576","YOU BOUGHT APPLE INC (AAPL) (Cash)",AAPL,'
            '"APPLE INC",Cash,0,,USD,252.56,10,0,,,,-757.68,04/06/2026'
        )
        row_qty11 = (
            '04/02/2026,"Taxable","Z29133576","YOU BOUGHT APPLE INC (AAPL) (Cash)",AAPL,'
            '"APPLE INC",Cash,0,,USD,252.56,11,0,,,,-757.68,04/06/2026'
        )
        csv1 = _write_csv(tmp_path / "a.csv", [row_qty10])
        csv2 = _write_csv(tmp_path / "b.csv", [row_qty11])

        ingest_fidelity_csv(db_path, csv1)
        ingest_fidelity_csv(db_path, csv2)

        conn = sqlite3.connect(str(db_path))
        qtys = sorted(r[0] for r in conn.execute(
            "SELECT quantity FROM fidelity_transactions WHERE symbol='AAPL'"
        ))
        conn.close()
        assert qtys == [10.0, 11.0], "different quantities produce different natural keys"

    def test_init_db_migrates_pre_existing_duplicates(self, tmp_path: Path) -> None:
        """Any DB created before the unique index existed may have duplicates on the
        natural key. init_db must clean them up idempotently so the unique index can
        be created, keeping the smallest-id row per natural key.

        Simulates a "legacy DB" by building the fidelity_transactions table by hand
        (no unique index), seeding duplicates, then invoking init_db to trigger the
        in-place migration.
        """
        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db_path))
        # Legacy schema: just the table, no natural-key unique index.
        conn.execute(
            """CREATE TABLE fidelity_transactions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date        TEXT NOT NULL,
                account         TEXT NOT NULL,
                account_number  TEXT NOT NULL,
                action          TEXT NOT NULL,
                action_type     TEXT NOT NULL DEFAULT '',
                symbol          TEXT NOT NULL DEFAULT '',
                description     TEXT NOT NULL DEFAULT '',
                lot_type        TEXT NOT NULL DEFAULT '',
                quantity        REAL NOT NULL DEFAULT 0,
                price           REAL NOT NULL DEFAULT 0,
                amount          REAL NOT NULL DEFAULT 0,
                settlement_date TEXT NOT NULL DEFAULT ''
            )"""
        )
        # Seed two rows with an identical natural key but different descriptions —
        # the "old" row has a smaller id and must be the survivor.
        conn.execute(
            """INSERT INTO fidelity_transactions
               (run_date, account, account_number, action, action_type, symbol,
                description, lot_type, quantity, price, amount, settlement_date)
               VALUES ('2026-04-02','Taxable','Z29133576','YOU BOUGHT APPLE INC (AAPL) (Cash)',
                       'buy','AAPL','APPLE INC','Cash',3.0,252.56,-757.68,'2026-04-06')""",
        )
        conn.execute(
            """INSERT INTO fidelity_transactions
               (run_date, account, account_number, action, action_type, symbol,
                description, lot_type, quantity, price, amount, settlement_date)
               VALUES ('2026-04-02','Taxable','Z29133576','YOU BOUGHT APPLE INC (AAPL) (Cash)',
                       'buy','AAPL','APPLE INC (DUPE)','Cash',3.0,252.56,-757.68,'2026-04-06')""",
        )
        conn.commit()
        pre = conn.execute("SELECT COUNT(*) FROM fidelity_transactions").fetchone()[0]
        conn.close()
        assert pre == 2  # sanity: seed really inserted two rows

        # init_db must clean up duplicates before the unique index gets created.
        init_db(db_path)

        conn = sqlite3.connect(str(db_path))
        post = conn.execute("SELECT COUNT(*) FROM fidelity_transactions").fetchone()[0]
        descriptions = [
            r[0]
            for r in conn.execute(
                "SELECT description FROM fidelity_transactions WHERE symbol='AAPL'"
            )
        ]
        # Verify the unique index now exists (migration completed end-to-end).
        idx_names = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='fidelity_transactions'"
            )
        ]
        conn.close()

        assert post == 1, "migration must collapse duplicates to a single row"
        # First-inserted (smaller id) wins — deterministic tie-break for observability.
        assert descriptions == ["APPLE INC"]
        assert "idx_fidelity_natural_key" in idx_names, "unique index must exist after migration"

    def test_init_db_is_idempotent_on_clean_db(self, tmp_path: Path) -> None:
        """init_db must be safe to re-run on a DB that already has the unique index
        and no duplicates."""
        db_path = tmp_path / "clean.db"
        init_db(db_path)
        init_db(db_path)  # must not raise
        init_db(db_path)  # third time for good measure

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM fidelity_transactions").fetchone()[0]
        conn.close()
        assert rows == 0
