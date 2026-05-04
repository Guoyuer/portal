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
from etl.sources.fidelity.parse import classify_fidelity_action  # noqa: E402
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
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("Portfolio_Positions_Apr-07-2026.csv", date(2026, 4, 7)),
            ("Portfolio_Positions_Dec-31-2025.csv", date(2025, 12, 31)),
            ("Portfolio_Positions_Jan-01-1999.csv", date(1999, 1, 1)),
            ("/some/dir/Portfolio_Positions_Aug-25-2025.csv", date(2025, 8, 25)),
            ("random.csv", None),
            ("Portfolio_Snapshot_Apr-07-2026.csv", None),
            ("Portfolio_Positions_XYZ-07-2026.csv", None),
        ],
    )
    def test_parse_as_of_from_filename(self, filename: str, expected: date | None) -> None:
        assert verify_positions.parse_as_of_from_filename(Path(filename)) == expected


# ── load_position_details ─────────────────────────────────────────────────────

class TestLoadPositionDetails:
    @pytest.mark.parametrize(
        ("rows", "expected"),
        [
            (
                [
                    {"Account Number": "Z001", "Symbol": "VOO", "Quantity": "10"},
                    {"Account Number": "Z001", "Symbol": "VOO", "Quantity": "5"},
                    {"Account Number": "Z001", "Symbol": "QQQM", "Quantity": "20"},
                ],
                {("Z001", "VOO"): 15.0, ("Z001", "QQQM"): 20.0},
            ),
            (
                [
                    {"Account Number": "Z001", "Symbol": "VOO", "Quantity": "10"},
                    {"Account Number": "Z001", "Symbol": "ZERO", "Quantity": "0"},
                ],
                {("Z001", "VOO"): 10.0},
            ),
            (
                [
                    {"Account Number": "", "Symbol": "VOO", "Quantity": "10"},
                    {"Account Number": "Z001", "Symbol": "", "Quantity": "10"},
                    {"Account Number": "Z001", "Symbol": "VOO", "Quantity": ""},
                    {"Account Number": "Z001", "Symbol": "GOOD", "Quantity": "3"},
                ],
                {("Z001", "GOOD"): 3.0},
            ),
            (
                [
                    {"Account Number": "Z001", "Symbol": "**Pending**", "Quantity": "1"},
                    {"Account Number": "Z001", "Symbol": "VOO", "Quantity": "2"},
                ],
                {("Z001", "VOO"): 2.0},
            ),
            (
                [{"Account Number": "Z001", "Symbol": "VOO", "Quantity": "1,234.5"}],
                {("Z001", "VOO"): 1234.5},
            ),
        ],
        ids=[
            "sums-lots",
            "skips-zero-quantity",
            "skips-blank-rows",
            "skips-total-rows",
            "comma-separated-quantity",
        ],
    )
    def test_load_position_details_quantities(
        self,
        tmp_path: Path,
        rows: list[dict[str, str]],
        expected: dict[tuple[str, str], float],
    ) -> None:
        csv_path = tmp_path / "pos.csv"
        _write_csv(csv_path, rows)
        details = verify_positions.load_position_details(csv_path)
        assert {key: detail.quantity for key, detail in details.items()} == expected


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
