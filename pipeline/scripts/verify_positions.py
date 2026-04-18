"""Verify: replay transactions -> compare computed share quantities vs positions snapshot.

Delegates replay to :func:`etl.replay.replay_transactions` using the
module-level :data:`etl.sources.fidelity.FIDELITY_REPLAY` config (same
config production's :func:`etl.sources.fidelity.positions_at` uses).
Exits non-zero on any intersection mismatch so the script is usable as
an automation gate.

Usage:
    python scripts/verify_positions.py --positions ~/Downloads/Portfolio_Positions_Apr-07-2026.csv
    python scripts/verify_positions.py --positions <path> --as-of 2026-04-07 --tolerance 0.05

If ``--as-of`` is omitted, the script parses the date from the filename
(e.g. ``Portfolio_Positions_Apr-07-2026.csv`` -> ``2026-04-07``). If that
parse fails, the replay is run across ALL transactions with no as-of cutoff.
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

_PROJECT_DIR = Path(__file__).resolve().parent.parent

# Make etl/ importable when invoked as a script.
sys.path.insert(0, str(_PROJECT_DIR))

import etl.dotenv_loader  # noqa: E402, F401  (side effect: load pipeline/.env)
from etl.replay import replay_transactions  # noqa: E402
from etl.sources.fidelity import FIDELITY_REPLAY  # noqa: E402

_DB_PATH = Path(os.environ.get("PORTAL_DB_PATH", str(_PROJECT_DIR / "data" / "timemachine.db")))

log = logging.getLogger(__name__)

# ── Filename-based as-of parsing ──────────────────────────────────────────────
_MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}
_FILENAME_PATTERN = re.compile(r"Portfolio_Positions_(\w{3})-(\d{2})-(\d{4})\.csv")


def parse_as_of_from_filename(path: Path) -> date | None:
    """Return YYYY-MM-DD date from 'Portfolio_Positions_Apr-07-2026.csv', or None."""
    m = _FILENAME_PATTERN.search(path.name)
    if not m:
        return None
    mon = _MONTH_MAP.get(m.group(1))
    if mon is None:
        return None
    try:
        return date(int(m.group(3)), mon, int(m.group(2)))
    except ValueError:
        return None


# ── Parse positions snapshot ──────────────────────────────────────────────────
def load_positions(path: Path) -> dict[tuple[str, str], float]:
    """Parse CSV. AGGREGATES quantity across multiple rows for same (acct, sym).

    Fidelity exports one row per lot type (e.g. Cash / Margin), so the same
    (account, symbol) can appear on multiple rows and must be summed.
    """
    positions: defaultdict[tuple[str, str], float] = defaultdict(float)
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            acct = (row.get("Account Number") or "").strip()
            sym = (row.get("Symbol") or "").strip()
            qty_s = (row.get("Quantity") or "").strip().replace(",", "")
            if not sym or not acct or not qty_s:
                continue
            if "**" in sym:  # total / pending rows
                continue
            try:
                qty = float(qty_s)
            except ValueError:
                continue
            if qty == 0:
                continue
            positions[(acct, sym)] += qty  # sum, not overwrite
    return dict(positions)


# ── CLI ───────────────────────────────────────────────────────────────────────
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Verify share counts: timemachine replay vs Portfolio_Positions snapshot"
    )
    p.add_argument("--positions", type=Path, required=True,
                   help="Path to Fidelity Portfolio_Positions_*.csv snapshot (required)")
    p.add_argument("--as-of", type=str, default=None,
                   help="Replay up to this date (YYYY-MM-DD). "
                        "If omitted, parsed from filename; if unparseable, replays all txns.")
    p.add_argument("--tolerance", type=float, default=0.05,
                   help="Per-(account,symbol) share-count tolerance (default 0.05). "
                        "Raised from 0.01 to tolerate ~0.01-0.02 DRIP rounding when a CSV "
                        "export is slightly stale; still catches real replay bugs.")
    return p.parse_args(argv)


def _resolve_as_of(args: argparse.Namespace) -> date | None:
    """Explicit --as-of wins; else parse filename; else None (replay all)."""
    if args.as_of:
        return date.fromisoformat(args.as_of)
    return parse_as_of_from_filename(args.positions)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args(argv)

    if not args.positions.exists():
        print(f"Error: positions CSV not found: {args.positions}", file=sys.stderr)
        return 1

    if not _DB_PATH.exists():
        print(f"Error: timemachine.db not found: {_DB_PATH}", file=sys.stderr)
        return 1

    as_of = _resolve_as_of(args)
    expected = load_positions(args.positions)
    # The script's contract says "all time" when --as-of is omitted and the
    # filename can't be parsed. The primitive needs a concrete date — use
    # today's date as the inclusive upper bound, which behaves identically
    # to legacy ``replay_from_db(as_of=None)`` for any DB whose latest
    # txn_date is on or before today.
    replay_as_of = as_of if as_of is not None else date.today()
    result = replay_transactions(_DB_PATH, FIDELITY_REPLAY, replay_as_of)
    computed: dict[tuple[str, str], float] = {key: st.quantity for key, st in result.positions.items()}

    as_of_str = str(as_of) if as_of else "all time"
    print(f"Portfolio_Positions verify (as-of {as_of_str}):")
    print(f"  computed {len(computed)} positions (timemachine replay)")
    print(f"  expected {len(expected)} positions from CSV")
    print()

    expected_keys = set(expected)
    computed_keys = set(computed)
    intersection = expected_keys & computed_keys
    only_csv = expected_keys - computed_keys
    only_computed = computed_keys - expected_keys

    matches: list[tuple[str, str, float, float, float]] = []
    mismatches: list[tuple[str, str, float, float, float]] = []
    for key in sorted(intersection):
        exp = expected[key]
        comp = computed[key]
        diff = comp - exp
        if abs(diff) <= args.tolerance:
            matches.append((key[0], key[1], exp, comp, diff))
        else:
            mismatches.append((key[0], key[1], exp, comp, diff))

    print(f"Intersection: {len(intersection)} positions")
    print(f"  {len(matches)} match (within +/-{args.tolerance})")
    if mismatches:
        print(f"  {len(mismatches)} mismatch:")
        for acct, sym, exp, comp, diff in mismatches:
            print(f"      {acct:<15} {sym:<8} expected {exp:>10.3f}  "
                  f"computed {comp:>10.3f}  diff {diff:+.3f}")
    print()

    if only_csv or only_computed:
        print("Non-intersecting (informational):")
        if only_csv:
            print("  ONLY IN CSV (not tracked by our history - likely Fidelity Crypto/Wealth):")
            for key in sorted(only_csv):
                acct, sym = key
                print(f"      {acct:<40} {sym:<8} {expected[key]:>10.3f}")
        if only_computed:
            print("  ONLY IN COMPUTED (shouldn't happen with MM_SYMBOLS exclusion):")
            for key in sorted(only_computed):
                acct, sym = key
                print(f"      {acct:<15} {sym:<8} {computed[key]:>10.3f}")
        print()

    if mismatches:
        print(f"FAIL: {len(mismatches)} mismatch beyond tolerance {args.tolerance}")
        return 1
    print(f"PASS: all {len(intersection)} intersecting positions within tolerance {args.tolerance}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
