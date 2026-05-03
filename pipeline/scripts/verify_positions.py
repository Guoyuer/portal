"""Verify: replay transactions -> compare computed share quantities vs positions snapshot.

Delegates replay to :func:`etl.replay.replay_transactions` using the
module-level :data:`etl.sources.fidelity.FIDELITY_REPLAY` config (same
config production's :func:`etl.sources.fidelity.positions_at` uses).
Exits non-zero on any intersection mismatch so the script is usable as
an automation gate.

Usage:
    python scripts/verify_positions.py --positions ~/Downloads/Portfolio_Positions_Apr-07-2026.csv
    python scripts/verify_positions.py --positions <path> --as-of 2026-04-07 --dollar-tolerance 1

If ``--as-of`` is omitted, the script parses the date from the filename
(e.g. ``Portfolio_Positions_Apr-07-2026.csv`` -> ``2026-04-07``). If that
parse fails, the replay is run across ALL transactions with no as-of cutoff.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

_PROJECT_DIR = Path(__file__).resolve().parent.parent

# Make etl/ importable when invoked as a script.
sys.path.insert(0, str(_PROJECT_DIR))

import etl.dotenv_loader  # noqa: E402, F401  (side effect: load pipeline/.env)
from etl.replay import replay_transactions  # noqa: E402
from etl.sources.fidelity import FIDELITY_REPLAY  # noqa: E402

_DB_PATH = Path(os.environ.get("PORTAL_DB_PATH", str(_PROJECT_DIR / "data" / "timemachine.db")))

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
@dataclass
class ExpectedPosition:
    quantity: float = 0.0
    last_price: float | None = None


def _parse_float_cell(raw: str | None) -> float | None:
    text = (raw or "").strip().replace(",", "").replace("$", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def load_position_details(path: Path) -> dict[tuple[str, str], ExpectedPosition]:
    """Parse CSV into share quantities plus optional last prices.

    Fidelity exports one row per lot type (e.g. Cash / Margin), so the same
    (account, symbol) can appear on multiple rows and must be summed.
    """
    positions: defaultdict[tuple[str, str], ExpectedPosition] = defaultdict(ExpectedPosition)
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            acct = (row.get("Account Number") or "").strip()
            sym = (row.get("Symbol") or "").strip()
            qty = _parse_float_cell(row.get("Quantity"))
            if not sym or not acct or qty is None:
                continue
            if "**" in sym:  # total / pending rows
                continue
            if qty == 0:
                continue

            detail = positions[(acct, sym)]
            detail.quantity += qty

            price = (
                _parse_float_cell(row.get("Last Price"))
                or _parse_float_cell(row.get("Current Price"))
                or _parse_float_cell(row.get("Price"))
            )
            if price is None:
                current_value = _parse_float_cell(row.get("Current Value"))
                price = abs(current_value / qty) if current_value is not None and qty else None
            if price is not None and price > 0:
                detail.last_price = price
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
    p.add_argument("--share-tolerance", type=float, default=0.001,
                   help="Per-(account,symbol) share-count fallback tolerance when no price is available "
                        "(default 0.001).")
    p.add_argument("--dollar-tolerance", type=float, default=1.0,
                   help="Per-(account,symbol) dollarized tolerance when the positions CSV has a price "
                        "(default $1).")
    return p.parse_args(argv)


def _resolve_as_of(args: argparse.Namespace) -> date | None:
    """Explicit --as-of wins; else parse filename; else None (replay all)."""
    if args.as_of:
        return date.fromisoformat(args.as_of)
    return parse_as_of_from_filename(args.positions)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if not args.positions.exists():
        print(f"Error: positions CSV not found: {args.positions}", file=sys.stderr)
        return 1

    if not _DB_PATH.exists():
        print(f"Error: timemachine.db not found: {_DB_PATH}", file=sys.stderr)
        return 1

    as_of = _resolve_as_of(args)
    expected_details = load_position_details(args.positions)
    expected = {key: detail.quantity for key, detail in expected_details.items()}
    # The script's contract says "all time" when --as-of is omitted and the
    # filename can't be parsed. The primitive needs a concrete date — use
    # today's date as the inclusive upper bound (effectively all rows).
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

    matches: list[tuple[str, str, float, float, float, float | None]] = []
    mismatches: list[tuple[str, str, float, float, float, float | None]] = []
    for key in sorted(intersection):
        exp = expected[key]
        comp = computed[key]
        diff = comp - exp
        price = expected_details[key].last_price
        dollar_diff = diff * price if price is not None else None
        if abs(diff) <= args.share_tolerance or (
            dollar_diff is not None and abs(dollar_diff) <= args.dollar_tolerance
        ):
            matches.append((key[0], key[1], exp, comp, diff, dollar_diff))
        else:
            mismatches.append((key[0], key[1], exp, comp, diff, dollar_diff))

    print(f"Intersection: {len(intersection)} positions")
    print(
        f"  {len(matches)} match "
        f"(within +/-${args.dollar_tolerance:.2f} when priced, else +/-{args.share_tolerance} shares)"
    )
    if mismatches:
        print(f"  {len(mismatches)} mismatch:")
        for acct, sym, exp, comp, diff, dollar_diff in mismatches:
            money = f"  dollar {dollar_diff:+.2f}" if dollar_diff is not None else ""
            print(f"      {acct:<15} {sym:<8} expected {exp:>10.3f}  "
                  f"computed {comp:>10.3f}  diff {diff:+.3f}{money}")
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
        print(
            f"FAIL: {len(mismatches)} mismatch beyond "
            f"${args.dollar_tolerance:.2f} / {args.share_tolerance} shares"
        )
        return 1
    print(
        f"PASS: all {len(intersection)} intersecting positions within "
        f"${args.dollar_tolerance:.2f} / {args.share_tolerance} shares"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
