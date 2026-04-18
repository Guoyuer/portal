"""Tests for Fidelity date parsing + DB ingestion via _ingest_one_csv."""

import sqlite3
from pathlib import Path

import pytest

from etl.db import init_db
from etl.parsing import parse_us_date
from etl.sources.fidelity import _ingest_one_csv


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
    """Tests for _ingest_one_csv — CSV → timemachine.db row writes."""

    @pytest.fixture()
    def db_path(self, tmp_path: Path) -> Path:
        p = tmp_path / "test.db"
        init_db(p)
        return p

    def test_ingest_sample_csv(self, db_path: Path, history_sample_csv: Path) -> None:
        count = _ingest_one_csv(db_path, history_sample_csv)
        assert count > 0
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM fidelity_transactions").fetchone()[0]
        conn.close()
        assert rows == count

    def test_overlap_replaces(self, db_path: Path, history_sample_csv: Path) -> None:
        _ingest_one_csv(db_path, history_sample_csv)
        count2 = _ingest_one_csv(db_path, history_sample_csv)
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM fidelity_transactions").fetchone()[0]
        conn.close()
        assert rows == count2  # replaced, not doubled

    def test_run_dates_normalized_to_iso(self, db_path: Path, history_sample_csv: Path) -> None:
        """Run dates must be stored as ISO YYYY-MM-DD, not raw MM/DD/YYYY."""
        import re
        _ingest_one_csv(db_path, history_sample_csv)
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
    """Range-replace semantics for _ingest_one_csv.

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
        count1 = _ingest_one_csv(db_path, history_sample_csv)
        count2 = _ingest_one_csv(db_path, history_sample_csv)
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

        _ingest_one_csv(db_path, full_csv)
        _ingest_one_csv(db_path, subset_csv)

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

        _ingest_one_csv(db_path, csv)

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

        _ingest_one_csv(db_path, csv_apr)
        _ingest_one_csv(db_path, csv_may)

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
