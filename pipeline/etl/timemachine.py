"""Timemachine: persist + recall Fidelity replay state, replay Qianji balances.

The Fidelity replay engine itself moved to :mod:`etl.replay`
(:func:`replay_transactions`) — what stays here is the support code that's
specific to the timemachine workflow: replay-state checkpointing,
positions-CSV calibration, Qianji reverse-replay, and the CLI.

Usage:
  python -m etl.timemachine 2024-06-15
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .db import get_connection
from .ingest.qianji_db import DEFAULT_DB_PATH, parse_qj_target_amount
from .types import parse_float as _float

#: Public re-export of the Qianji DB path. Module-level assignment makes mypy
#: --strict accept the re-export (which `... as ...` did not under strict).
DEFAULT_QJ_DB = DEFAULT_DB_PATH

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Money market funds ($1/share, treated as cash). Re-exported here for the
# checkpoint + calibration helpers; the canonical definition (and the one
# the replay primitive consumes) lives in :mod:`etl.sources.fidelity`.
MM_SYMBOLS = frozenset({"SPAXX", "FZFXX", "FDRXX"})


# ── Checkpoint save/load ──────────────────────────────────────────────────────


def save_checkpoint(db_path: Path, replay_result: dict[str, Any]) -> None:
    """Persist replay state as a checkpoint for future incremental builds."""
    as_of = str(replay_result["as_of"])
    # Serialize tuple keys: (account, symbol) → "account|symbol"
    positions = {f"{k[0]}|{k[1]}": v for k, v in replay_result["positions"].items()}
    cost_basis = {f"{k[0]}|{k[1]}": v for k, v in replay_result["cost_basis"].items()}
    cash = dict(replay_result["cash"])  # already string keys

    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO replay_checkpoint (date, positions, cash, cost_basis) VALUES (?, ?, ?, ?)",
            (as_of, json.dumps(positions), json.dumps(cash), json.dumps(cost_basis)),
        )
        conn.commit()
    finally:
        conn.close()


def load_checkpoint(db_path: Path) -> dict[str, Any] | None:
    """Load the latest replay checkpoint, or None if no checkpoint exists."""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT date, positions, cash, cost_basis FROM replay_checkpoint ORDER BY date DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    as_of_str, pos_json, cash_json, cb_json = row
    # Deserialize: "account|symbol" → (account, symbol)
    raw_positions = json.loads(pos_json)
    positions = {tuple(k.split("|", 1)): v for k, v in raw_positions.items()}
    raw_cb = json.loads(cb_json)
    cost_basis = {tuple(k.split("|", 1)): v for k, v in raw_cb.items()}
    cash = json.loads(cash_json)

    return {
        "positions": positions,
        "cost_basis": cost_basis,
        "cash": cash,
        "as_of": date.fromisoformat(as_of_str),
        "txn_count": 0,  # unknown from checkpoint
    }


# ── Positions CSV calibration ────────────────────────────────────────────────


def calibrate_from_positions(
    db_path: Path,
    csv_path: Path,
    replay_result: dict[str, Any],
) -> dict[str, Any]:
    """Compare replay state against Fidelity positions CSV, log drift, return calibrated state.

    Returns a new replay_result dict with positions/cost_basis overwritten by CSV ground truth.
    """
    from .parsing import read_csv_rows

    csv_positions: dict[tuple[str, str], float] = {}  # (account, symbol) -> qty
    csv_cost_basis: dict[tuple[str, str], float] = {}

    for row in read_csv_rows(csv_path):
        acct = (row.get("Account Number") or "").strip()
        sym = (row.get("Symbol") or "").strip()
        qty_str = (row.get("Quantity") or "").strip()
        cb_str = (row.get("Cost Basis Total") or "").strip().replace("$", "").replace(",", "")

        if not acct or not sym or sym in MM_SYMBOLS:
            continue
        if sym.startswith("**"):  # total row
            continue

        qty = _float(qty_str)
        cb = _float(cb_str) if cb_str and cb_str != "--" else 0.0

        if qty > 0.001:
            key = (acct, sym)
            csv_positions[key] = qty
            csv_cost_basis[key] = abs(cb)

    # Compare with replay
    replay_pos: dict[tuple[str, str], float] = dict(replay_result["positions"])
    replay_cb: dict[tuple[str, str], float] = dict(replay_result["cost_basis"])

    details: list[dict[str, Any]] = []
    positions_ok = 0
    total_cb_drift = 0.0
    total_csv_cb = 0.0

    all_keys = set(csv_positions.keys()) | set(replay_pos.keys())
    for key in sorted(all_keys):
        csv_qty = csv_positions.get(key, 0.0)
        csv_cb = csv_cost_basis.get(key, 0.0)
        rep_qty = replay_pos.get(key, 0.0)
        rep_cb = replay_cb.get(key, 0.0)

        qty_match = abs(csv_qty - rep_qty) < 0.01
        cb_drift = csv_cb - rep_cb

        if qty_match and abs(cb_drift) < 1.0:
            positions_ok += 1
        else:
            details.append({
                "account": key[0],
                "symbol": key[1],
                "csv_qty": round(csv_qty, 4),
                "replay_qty": round(rep_qty, 4),
                "csv_cb": round(csv_cb, 2),
                "replay_cb": round(rep_cb, 2),
                "cb_drift": round(cb_drift, 2),
            })

        total_cb_drift += cb_drift
        total_csv_cb += csv_cb

    total_cb_pct = (total_cb_drift / total_csv_cb * 100) if total_csv_cb else 0.0

    # Log to calibration_log
    today = date.today().isoformat()

    conn = get_connection(db_path)
    try:
        # Check days since last calibration
        last_row = conn.execute("SELECT date FROM calibration_log ORDER BY date DESC LIMIT 1").fetchone()
        days_since = (date.today() - date.fromisoformat(last_row[0])).days if last_row else 0

        conn.execute(
            "INSERT OR REPLACE INTO calibration_log (date, days_since_last, total_cb_drift, total_cb_pct,"
            " positions_ok, positions_total, details) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (today, days_since, round(total_cb_drift, 2), round(total_cb_pct, 2),
             positions_ok, len(all_keys), json.dumps(details)),
        )
        conn.commit()
    finally:
        conn.close()

    # Print summary
    print(f"  Calibration: {positions_ok}/{len(all_keys)} positions match")
    if details:
        print(f"  Total cost basis drift: ${total_cb_drift:,.2f} ({total_cb_pct:.1f}%)")
        for d in details[:5]:
            print(f"    {d['symbol']}: qty {d['replay_qty']}->{d['csv_qty']},"
                  f" CB ${d['replay_cb']:,.2f}->${d['csv_cb']:,.2f} (delta ${d['cb_drift']:+,.2f})")
        if len(details) > 5:
            print(f"    ... and {len(details) - 5} more")

    # Return calibrated state (CSV ground truth overwrites replay)
    calibrated: dict[str, Any] = {
        "positions": {**replay_pos, **csv_positions},  # CSV overwrites
        "cost_basis": {**replay_cb, **csv_cost_basis},
        "cash": dict(replay_result["cash"]),
        "as_of": replay_result["as_of"],
        "txn_count": replay_result["txn_count"],
    }

    # Remove positions that are in replay but not in CSV (sold since last replay)
    cal_pos: dict[tuple[str, str], float] = calibrated["positions"]
    cal_cb: dict[tuple[str, str], float] = calibrated["cost_basis"]
    for key in list(cal_pos.keys()):
        if key not in csv_positions and key in replay_pos:
            del cal_pos[key]
            if key in cal_cb:
                del cal_cb[key]

    # Save calibrated state as checkpoint
    save_checkpoint(db_path, calibrated)

    return calibrated


# ── Qianji replay ─────────────────────────────────────────────────────────────


def replay_qianji(db_path: Path, as_of: date | None = None) -> dict[str, float]:
    """Replay Qianji account balances at as_of date.

    Strategy: start from current balances (user_asset), reverse transactions
    after as_of. Each account balance stays in its native currency.

    Qianji conventions:
      - expense  (type 0): fromact loses money
      - income   (type 1): fromact gains money
      - transfer (type 2): fromact→targetact (cross-currency uses extra.curr.tv)
      - repayment(type 3): same as transfer
    """
    if not db_path.exists():
        log.warning("Qianji DB not found: %s", db_path)
        return {}

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        # Current balances and currencies
        balances: dict[str, float] = {}
        currencies: dict[str, str] = {}
        for name, money, currency in conn.execute(
            "SELECT name, money, currency FROM user_asset WHERE status = 0"
        ):
            balances[name] = float(money)
            currencies[name] = currency or "USD"

        if as_of is None:
            return balances

        # Reverse all transactions after end of as_of day
        cutoff = datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59, tzinfo=UTC).timestamp()

        for bill_type, money, fromact, targetact, extra_str in conn.execute(
            "SELECT type, money, fromact, targetact, extra "
            "FROM user_bill WHERE status = 1 AND time > ? ORDER BY time",
            (cutoff,),
        ):
            money = float(money)
            fromact = fromact or ""
            targetact = targetact or ""
            tv = parse_qj_target_amount(money, extra_str)

            if bill_type == 0:  # expense: fromact lost money → add back
                if fromact:
                    balances[fromact] = balances.get(fromact, 0) + money
            elif bill_type == 1:  # income: fromact gained money → subtract
                if fromact:
                    balances[fromact] = balances.get(fromact, 0) - money
            elif bill_type in (2, 3):  # transfer/repayment: reverse both sides
                if fromact:
                    balances[fromact] = balances.get(fromact, 0) + money
                if targetact:
                    balances[targetact] = balances.get(targetact, 0) - tv

        return balances
    finally:
        conn.close()


def replay_qianji_currencies(db_path: Path) -> dict[str, str]:
    """Return {account_name: currency} from Qianji DB."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return {
            name: (currency or "USD")
            for name, currency in conn.execute("SELECT name, currency FROM user_asset WHERE status = 0")
        }
    finally:
        conn.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    p = argparse.ArgumentParser(description="Timemachine: replay Fidelity + Qianji")
    p.add_argument("date", nargs="?", help="Replay as of YYYY-MM-DD (defaults to today)")
    p.add_argument(
        "--db",
        default="pipeline/data/timemachine.db",
        help="Path to timemachine SQLite DB (default: pipeline/data/timemachine.db)",
    )
    p.add_argument("--qianji-db", default=str(DEFAULT_QJ_DB), help="Path to Qianji SQLite DB")
    args = p.parse_args()

    # Deferred import sidesteps the ``etl.replay`` ↔ ``etl.sources`` cycle.
    from etl.replay import replay_transactions
    from etl.sources.fidelity import MM_SYMBOLS as _MM
    from etl.sources.fidelity import TABLE as _T

    db = Path(args.db)
    qj_db = Path(args.qianji_db)
    as_of = date.fromisoformat(args.date) if args.date else date.today()

    result = replay_transactions(
        db, _T, as_of,
        date_col="run_date", ticker_col="symbol", amount_col="amount",
        account_col="account_number",
        exclude_tickers=_MM,
        track_cash=True, lot_type_col="lot_type",
        mm_drip_tickers=_MM,
    )

    print(f"As of: {as_of}")
    print(f"\nFidelity positions ({len(result.positions)} holdings):")
    for (acct, sym), st in sorted(result.positions.items()):
        print(f"  {acct}  {sym:<8} {st.quantity:>12.3f}")
    print("\nFidelity cash:")
    for acct, bal in sorted(result.cash.items()):
        print(f"  {acct}  ${bal:>12.2f}")

    # Qianji
    qj_balances = replay_qianji(qj_db, as_of)
    if qj_balances:
        currencies = replay_qianji_currencies(qj_db)
        print(f"\nQianji accounts ({len([b for b in qj_balances.values() if abs(b) >= 0.01])} with balance):")
        for acct, bal in sorted(qj_balances.items(), key=lambda x: -abs(x[1])):
            if abs(bal) >= 0.01:
                curr = currencies.get(acct, "USD")
                sym = "¥" if curr == "CNY" else "$"
                print(f"  {acct:<25} {sym}{bal:>12.2f}")


if __name__ == "__main__":
    main()
