"""Tests for scripts/verify_positions.py — filename parsing, CSV aggregation,
and the end-to-end gate behaviour (pass / fail / informational-only)."""
from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "pipeline"))

from etl.db import get_connection  # noqa: E402
from etl.sources.fidelity import classify_fidelity_action  # noqa: E402
from scripts import verify_positions  # noqa: E402

# ── Helpers ──────────────────────────────────────────────────────────────────

def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Write a minimal Fidelity positions CSV with the columns we read."""
    fields = ["Account Number", "Symbol", "Description", "Quantity", "Last Price", "Current Value"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            full = {k: row.get(k, "") for k in fields}
            w.writerow(full)


def _seed_db(db_path: Path, txns: list[tuple[str, str, str, str, str, float, float]]) -> None:
    """Seed fidelity_transactions rows into an already schema-initialized DB.

    Each tuple: (run_date, account_number, action, symbol, lot_type, quantity, amount).
    Populates ``action_kind`` via :func:`classify_fidelity_action` so the
    rows are visible to :func:`etl.replay.replay_transactions` (production
    ingest does the same).
    """
    conn = get_connection(db_path)
    conn.executemany(
        "INSERT INTO fidelity_transactions "
        "(run_date, account_number, action, action_kind, symbol, lot_type, quantity, price, amount) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
        [
            (run_date, acct, action, classify_fidelity_action(action).value,
             sym, lot_type, qty, amt)
            for run_date, acct, action, sym, lot_type, qty, amt in txns
        ],
    )
    conn.commit()
    conn.close()


# ── parse_as_of_from_filename ────────────────────────────────────────────────

class TestParseAsOfFromFilename:
    def test_apr_07_2026(self) -> None:
        assert verify_positions.parse_as_of_from_filename(
            Path("Portfolio_Positions_Apr-07-2026.csv")
        ) == date(2026, 4, 7)

    def test_dec_31_2025(self) -> None:
        assert verify_positions.parse_as_of_from_filename(
            Path("Portfolio_Positions_Dec-31-2025.csv")
        ) == date(2025, 12, 31)

    def test_jan_01_1999(self) -> None:
        assert verify_positions.parse_as_of_from_filename(
            Path("Portfolio_Positions_Jan-01-1999.csv")
        ) == date(1999, 1, 1)

    def test_pattern_anywhere_in_path(self) -> None:
        """Regex uses .search so parent dirs don't matter."""
        assert verify_positions.parse_as_of_from_filename(
            Path("/some/dir/Portfolio_Positions_Aug-25-2025.csv")
        ) == date(2025, 8, 25)

    def test_non_matching_returns_none(self) -> None:
        assert verify_positions.parse_as_of_from_filename(Path("random.csv")) is None
        assert verify_positions.parse_as_of_from_filename(
            Path("Portfolio_Snapshot_Apr-07-2026.csv")
        ) is None

    def test_bad_month_returns_none(self) -> None:
        assert verify_positions.parse_as_of_from_filename(
            Path("Portfolio_Positions_XYZ-07-2026.csv")
        ) is None


# ── load_positions ────────────────────────────────────────────────────────────

class TestLoadPositions:
    def test_aggregates_cash_and_margin_lots(self, tmp_path: Path) -> None:
        """Two rows for the same (account, symbol) must be summed, not overwritten."""
        csv_path = tmp_path / "pos.csv"
        _write_csv(csv_path, [
            {"Account Number": "Z001", "Symbol": "VOO", "Quantity": "10"},
            {"Account Number": "Z001", "Symbol": "VOO", "Quantity": "5"},  # second lot type
            {"Account Number": "Z001", "Symbol": "QQQM", "Quantity": "20"},
        ])
        positions = verify_positions.load_positions(csv_path)
        assert positions[("Z001", "VOO")] == pytest.approx(15.0)
        assert positions[("Z001", "QQQM")] == pytest.approx(20.0)

    def test_skips_zero_quantity(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "pos.csv"
        _write_csv(csv_path, [
            {"Account Number": "Z001", "Symbol": "VOO", "Quantity": "10"},
            {"Account Number": "Z001", "Symbol": "ZERO", "Quantity": "0"},
        ])
        positions = verify_positions.load_positions(csv_path)
        assert ("Z001", "ZERO") not in positions
        assert ("Z001", "VOO") in positions

    def test_skips_blank_rows(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "pos.csv"
        _write_csv(csv_path, [
            {"Account Number": "", "Symbol": "VOO", "Quantity": "10"},       # no acct
            {"Account Number": "Z001", "Symbol": "", "Quantity": "10"},       # no sym
            {"Account Number": "Z001", "Symbol": "VOO", "Quantity": ""},      # no qty
            {"Account Number": "Z001", "Symbol": "GOOD", "Quantity": "3"},
        ])
        positions = verify_positions.load_positions(csv_path)
        assert positions == {("Z001", "GOOD"): 3.0}

    def test_skips_total_rows(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "pos.csv"
        _write_csv(csv_path, [
            {"Account Number": "Z001", "Symbol": "**Pending**", "Quantity": "1"},
            {"Account Number": "Z001", "Symbol": "VOO", "Quantity": "2"},
        ])
        positions = verify_positions.load_positions(csv_path)
        assert ("Z001", "**Pending**") not in positions
        assert positions == {("Z001", "VOO"): 2.0}

    def test_handles_comma_separated_qty(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "pos.csv"
        _write_csv(csv_path, [
            {"Account Number": "Z001", "Symbol": "VOO", "Quantity": "1,234.5"},
        ])
        positions = verify_positions.load_positions(csv_path)
        assert positions[("Z001", "VOO")] == pytest.approx(1234.5)


# ── Integration: main() with seeded DB + CSV ─────────────────────────────────

class TestMainIntegration:
    def _run(self, csv_path: Path, db_path: Path, monkeypatch,
             extra_args: tuple[str, ...] = ()) -> int:
        monkeypatch.setattr(verify_positions, "_DB_PATH", db_path)
        argv = ["--positions", str(csv_path), *extra_args]
        return verify_positions.main(argv)

    def test_exact_match_pass(self, tmp_path: Path, empty_db: Path, monkeypatch, capsys) -> None:
        _seed_db(empty_db, [
            ("2026-01-05", "Z001", "YOU BOUGHT VOO", "VOO", "Cash", 10.0, -4500.0),
        ])
        csv_path = tmp_path / "Portfolio_Positions_Apr-07-2026.csv"
        _write_csv(csv_path, [
            {"Account Number": "Z001", "Symbol": "VOO", "Quantity": "10"},
        ])
        rc = self._run(csv_path, empty_db, monkeypatch)
        assert rc == 0
        out = capsys.readouterr().out
        assert "PASS" in out
        assert "2026-04-07" in out

    def test_mismatch_beyond_dollar_tolerance_fails(
        self, tmp_path: Path, empty_db: Path, monkeypatch, capsys,
    ) -> None:
        _seed_db(empty_db, [
            ("2026-01-05", "Z001", "YOU BOUGHT VOO", "VOO", "Cash", 10.0, -4500.0),
        ])
        csv_path = tmp_path / "Portfolio_Positions_Apr-07-2026.csv"
        _write_csv(csv_path, [
            {"Account Number": "Z001", "Symbol": "VOO", "Quantity": "10.5", "Last Price": "100"},
        ])
        rc = self._run(csv_path, empty_db, monkeypatch)
        assert rc == 1
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "1 mismatch" in out
        assert "dollar" in out

    def test_share_diff_passes_when_dollar_diff_is_small(
        self, tmp_path: Path, empty_db: Path, monkeypatch,
    ) -> None:
        _seed_db(empty_db, [
            ("2026-01-05", "Z001", "YOU BOUGHT VOO", "VOO", "Cash", 10.0, -4500.0),
        ])
        csv_path = tmp_path / "Portfolio_Positions_Apr-07-2026.csv"
        _write_csv(csv_path, [
            {"Account Number": "Z001", "Symbol": "VOO", "Quantity": "10.004", "Last Price": "100"},
        ])
        rc = self._run(csv_path, empty_db, monkeypatch)
        assert rc == 0

    def test_small_share_diff_fails_when_dollar_diff_is_material(
        self, tmp_path: Path, empty_db: Path, monkeypatch,
    ) -> None:
        _seed_db(empty_db, [
            ("2026-01-05", "Z001", "YOU BOUGHT MAR", "MAR", "Cash", 6.091, -1470.63),
        ])
        csv_path = tmp_path / "Portfolio_Positions_Apr-07-2026.csv"
        _write_csv(csv_path, [
            {"Account Number": "Z001", "Symbol": "MAR", "Quantity": "6.106", "Last Price": "354.97"},
        ])
        rc = self._run(csv_path, empty_db, monkeypatch)
        assert rc == 1

    def test_share_tolerance_fallback_when_no_price(self, tmp_path: Path, empty_db: Path, monkeypatch) -> None:
        _seed_db(empty_db, [
            ("2026-01-05", "Z001", "YOU BOUGHT VOO", "VOO", "Cash", 10.0, -4500.0),
        ])
        csv_path = tmp_path / "Portfolio_Positions_Apr-07-2026.csv"
        _write_csv(csv_path, [
            {"Account Number": "Z001", "Symbol": "VOO", "Quantity": "10.002"},
        ])
        rc = self._run(csv_path, empty_db, monkeypatch)
        assert rc == 1

    def test_csv_only_keys_are_informational(self, tmp_path: Path, empty_db: Path, monkeypatch, capsys) -> None:
        """Keys only in CSV (not in computed) are reported, don't cause failure."""
        _seed_db(empty_db, [
            ("2026-01-05", "Z001", "YOU BOUGHT VOO", "VOO", "Cash", 10.0, -4500.0),
        ])
        csv_path = tmp_path / "Portfolio_Positions_Apr-07-2026.csv"
        _write_csv(csv_path, [
            {"Account Number": "Z001", "Symbol": "VOO", "Quantity": "10"},
            # UUID-account position our history doesn't know about:
            {"Account Number": "2ad9d14c-xxx", "Symbol": "ETH", "Quantity": "0.5"},
        ])
        rc = self._run(csv_path, empty_db, monkeypatch)
        assert rc == 0
        out = capsys.readouterr().out
        assert "ONLY IN CSV" in out
        assert "ETH" in out

    def test_as_of_cli_overrides_filename(self, tmp_path: Path, empty_db: Path, monkeypatch, capsys) -> None:
        """--as-of flag wins over filename parsing (used for non-standard paths)."""
        # Txn AFTER filename's Apr-07, BEFORE CLI override's Jun-01:
        _seed_db(empty_db, [
            ("2026-01-05", "Z001", "YOU BOUGHT VOO", "VOO", "Cash", 10.0, -4500.0),
            ("2026-05-10", "Z001", "YOU BOUGHT VOO", "VOO", "Cash", 5.0, -2500.0),
        ])
        csv_path = tmp_path / "Portfolio_Positions_Apr-07-2026.csv"
        _write_csv(csv_path, [
            {"Account Number": "Z001", "Symbol": "VOO", "Quantity": "15"},
        ])
        rc = self._run(csv_path, empty_db, monkeypatch, extra_args=("--as-of", "2026-06-01"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "2026-06-01" in out

    def test_mm_symbols_excluded_from_computed(self, tmp_path: Path, empty_db: Path, monkeypatch, capsys) -> None:
        """SPAXX (money market) must NOT appear in computed positions —
        confirms the script wires ``MM_SYMBOLS`` into ``replay_transactions``
        (matches Fidelity-source behaviour)."""
        _seed_db(empty_db, [
            ("2026-01-05", "Z001", "YOU BOUGHT VOO", "VOO", "Cash", 10.0, -4500.0),
            ("2026-01-06", "Z001", "REINVESTMENT SPAXX", "SPAXX", "Cash", 100.0, -100.0),
        ])
        csv_path = tmp_path / "Portfolio_Positions_Apr-07-2026.csv"
        _write_csv(csv_path, [
            {"Account Number": "Z001", "Symbol": "VOO", "Quantity": "10"},
        ])
        rc = self._run(csv_path, empty_db, monkeypatch)
        assert rc == 0
        out = capsys.readouterr().out
        # SPAXX must not surface as an "ONLY IN COMPUTED" surprise.
        assert "SPAXX" not in out
