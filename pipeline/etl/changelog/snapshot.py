"""DB state snapshot + diff.

Flow: :func:`capture` reads the local DB into a :class:`SyncSnapshot`. Two
snapshots (before / after a sync run) feed :func:`diff` to produce a
:class:`SyncChangelog` describing what rows appeared. The changelog is what
the render layer turns into an email body.

Snapshot contents are intentionally asymmetric — full tuple sets for small
tables (so we can diff exact rows) and aggregate counts for big tables
(``daily_close``, ``econ_series``) where per-row detail is noise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..db import get_readonly_connection

# ── Snapshot ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NetWorthPoint:
    """One row of ``computed_daily`` with all component splits preserved.

    Convention (matches :func:`etl.allocation.compute_daily_allocation`):
      - ``total`` = sum of positive asset categories (us+non_us+crypto+safe_net)
      - ``liabilities`` = sum of negative positions (itself stored as negative)
      - real "net worth" = ``total + liabilities`` (the frontend's ``netWorth``)

    The email header row ("Total:") shows :attr:`net_worth` so the label
    matches the block's name; the drift flag checks the allocation invariant
    that ``total == asset_sum``.
    """

    total: float
    us_equity: float
    non_us_equity: float
    crypto: float
    safe_net: float
    liabilities: float

    @property
    def asset_sum(self) -> float:
        """Should equal stored ``total`` if the allocation pipeline is consistent."""
        return self.us_equity + self.non_us_equity + self.crypto + self.safe_net

    @property
    def net_worth(self) -> float:
        """Assets minus liabilities (matches frontend ``netWorth`` in ``compute.ts``)."""
        return self.total + self.liabilities


@dataclass(frozen=True)
class SyncSnapshot:
    """Captured state of the local DB at one point in time.

    Small tables keep full tuple sets so ``diff`` can enumerate new rows.
    Large tables (``daily_close``, ``econ_series``) keep counts + bounds only.
    """

    # Small tables — full tuple sets / dicts for exact diff
    # Fidelity: (run_date, action_type, symbol, quantity, amount). Stable
    # across runs because Fidelity rows read deterministic USD amounts from
    # the CSV — no live FX conversion — so content-tuple identity doesn't drift.
    fidelity_txns: frozenset[tuple[str, str, str, float, float]] = field(default_factory=frozenset)
    # Qianji: frozenset of (date, type, category, amount, note). Content-tuple
    # identity works because ``parse_qj_amount`` now resolves CNY→USD via
    # per-bill-date historical rates (see ``etl/ingest/qianji_db.py``) — the
    # amount doesn't drift run-to-run, so set difference is ghost-free.
    # ``note`` is part of the tuple so the email can expand low-count
    # categories (Salary, 401k, ...) with date+note detail.
    qianji_txns: frozenset[tuple[str, str, str, float, str]] = field(
        default_factory=frozenset,
    )
    # date -> NetWorthPoint (computed_daily is small: ~1 row/day). Component
    # splits are captured so the email can show per-category Δ and check
    # components sum to total.
    computed_daily: dict[str, NetWorthPoint] = field(default_factory=dict)

    # Large tables — aggregates only
    daily_close_count: int = 0
    daily_close_max_date: str = ""
    econ_series_keys: frozenset[str] = field(default_factory=frozenset)
    empower_snapshots_count: int = 0
    # Latest 401k snapshot total (sum of mktval across funds). None when no
    # snapshots exist yet. Exposed so the email can show the $ delta in
    # addition to the "+N snapshots" count.
    empower_latest_value: float | None = None


def capture(db_path: Path) -> SyncSnapshot:
    """Read the local DB and build a :class:`SyncSnapshot`.

    Returns an empty snapshot if the DB file does not exist yet (e.g. the build
    step failed before the file was created). Every query is read-only.
    """
    if not db_path.exists():
        return SyncSnapshot()

    conn = get_readonly_connection(db_path)
    try:
        fidelity = frozenset(
            (str(r[0]), str(r[1]), str(r[2]), float(r[3]), float(r[4]))
            for r in conn.execute(
                "SELECT run_date, action_type, symbol, quantity, amount "
                "FROM fidelity_transactions"
            )
        )
        qianji_txns = frozenset(
            (str(r[0]), str(r[1]), str(r[2]), float(r[3]), str(r[4] or ""))
            for r in conn.execute(
                "SELECT date, type, category, amount, note FROM qianji_transactions"
            )
        )
        computed_daily = {
            str(r[0]): NetWorthPoint(
                total=float(r[1]),
                us_equity=float(r[2]),
                non_us_equity=float(r[3]),
                crypto=float(r[4]),
                safe_net=float(r[5]),
                liabilities=float(r[6]),
            )
            for r in conn.execute(
                "SELECT date, total, us_equity, non_us_equity, "
                "crypto, safe_net, liabilities FROM computed_daily"
            )
        }
        dc_row = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(date), '') FROM daily_close"
        ).fetchone()
        dc_count = int(dc_row[0]) if dc_row else 0
        dc_max = str(dc_row[1]) if dc_row else ""
        econ_keys = frozenset(
            str(r[0]) for r in conn.execute("SELECT DISTINCT key FROM econ_series")
        )
        emp_count_row = conn.execute(
            "SELECT COUNT(*) FROM empower_snapshots"
        ).fetchone()
        emp_count = int(emp_count_row[0]) if emp_count_row else 0
        # Latest snapshot's total $ value. COALESCE drops NULL when no funds.
        emp_value_row = conn.execute(
            "SELECT SUM(f.mktval) FROM empower_funds f "
            "WHERE f.snapshot_id = ("
            "  SELECT id FROM empower_snapshots ORDER BY snapshot_date DESC LIMIT 1"
            ")"
        ).fetchone()
        emp_value: float | None = (
            float(emp_value_row[0]) if emp_value_row and emp_value_row[0] is not None else None
        )
    finally:
        conn.close()

    return SyncSnapshot(
        fidelity_txns=fidelity,
        qianji_txns=qianji_txns,
        computed_daily=computed_daily,
        daily_close_count=dc_count,
        daily_close_max_date=dc_max,
        econ_series_keys=econ_keys,
        empower_snapshots_count=emp_count,
        empower_latest_value=emp_value,
    )


# ── Changelog ────────────────────────────────────────────────────────────────


@dataclass
class SyncChangelog:
    """What changed between two snapshots.

    ``fidelity_added`` / ``computed_daily_added`` enumerate new rows; bigger
    tables expose a count delta only. ``econ_refreshed`` is True only when the
    FRED key *set* changes (new indicator added / removed) — not on every run,
    because FRED normally has a stable set and a full-replace-per-run would
    otherwise make this noise on every successful sync.
    """

    # (run_date, action_type, symbol, quantity, amount), sorted by run_date
    fidelity_added: list[tuple[str, str, str, float, float]] = field(default_factory=list)
    qianji_added_count: int = 0
    # category -> (count, total_amount). Used for the headline per-cat line.
    qianji_added_by_category: dict[str, tuple[int, float]] = field(default_factory=dict)
    # category -> [(date, amount, note), ...] for each added row. The renderer
    # expands the detail for small categories (count ≤ 2) so one-offs like
    # Salary or 401k can be verified at a glance without opening the DB.
    qianji_added_rows_by_category: dict[str, list[tuple[str, float, str]]] = field(
        default_factory=dict
    )
    # new date -> total (dates that appeared in "after" but not "before")
    computed_daily_added: dict[str, float] = field(default_factory=dict)
    daily_close_added: int = 0
    daily_close_max_after: str = ""
    econ_refreshed: bool = False
    econ_keys_added: list[str] = field(default_factory=list)
    econ_keys_removed: list[str] = field(default_factory=list)
    empower_added: int = 0
    # $ change in latest 401k total value (sum of mktval across funds).
    # None when either endpoint has no Empower data (common on early days
    # before QFX ingest succeeded, or test fixtures that don't populate it).
    empower_value_delta: float | None = None
    empower_value_before: float | None = None
    empower_value_after: float | None = None
    net_worth_before: float | None = None
    net_worth_after: float | None = None
    net_worth_delta: float | None = None
    # Full component split at each endpoint — enables per-category Δ and
    # ``component_sum vs total`` consistency flag in the email.
    net_worth_point_before: NetWorthPoint | None = None
    net_worth_point_after: NetWorthPoint | None = None
    # Dates of the "latest" computed_daily row before/after — used to render
    # "Unchanged" net worth blocks when both endpoints land on the same date.
    net_worth_before_date: str | None = None
    net_worth_after_date: str | None = None

    def has_meaningful_changes(self) -> bool:
        """True if this changelog represents a sync that did actual work.

        FRED refresh alone is NOT meaningful — every run touches ``econ_series``
        because the import is a full replace, so it would trigger noise on
        silent runs.
        """
        return bool(
            self.fidelity_added
            or self.qianji_added_count > 0
            or self.computed_daily_added
            or self.daily_close_added > 0
            or self.empower_added > 0
        )

    def net_worth_delta_pct(self) -> float | None:
        """Return delta as % of before (None if either endpoint is missing or before == 0)."""
        if self.net_worth_before is None or self.net_worth_after is None:
            return None
        if self.net_worth_before == 0:
            return None
        return (self.net_worth_after - self.net_worth_before) / self.net_worth_before * 100


def diff(before: SyncSnapshot, after: SyncSnapshot) -> SyncChangelog:
    """Compute the set difference between two snapshots."""
    # Fidelity: sorted by run_date (first tuple element) for stable email output
    fidelity_added = sorted(
        after.fidelity_txns - before.fidelity_txns,
        key=lambda row: (row[0], row[1], row[2]),
    )

    # Qianji: set difference on (date, type, category, amount, note). Works
    # because ``parse_qj_amount`` uses per-bill-date historical CNY rates, so
    # the ``amount`` of a bill doesn't drift run-to-run — any tuple in AFTER
    # but not BEFORE is a genuinely new (or edited) bill.
    qianji_added_rows = after.qianji_txns - before.qianji_txns
    qianji_by_cat: dict[str, tuple[int, float]] = {}
    qianji_rows_by_cat: dict[str, list[tuple[str, float, str]]] = {}
    for _date, _type, category, amount, note in qianji_added_rows:
        count, total = qianji_by_cat.get(category, (0, 0.0))
        qianji_by_cat[category] = (count + 1, total + amount)
        qianji_rows_by_cat.setdefault(category, []).append((_date, amount, note))
    qianji_added_count = len(qianji_added_rows)
    for rows in qianji_rows_by_cat.values():
        rows.sort(key=lambda r: r[0])

    # computed_daily: dates that weren't in before. Value kept as float (the
    # ``total``) because the only consumers (``has_meaningful_changes`` and a
    # couple of existing tests) care about the scalar, not the components.
    before_dates = set(before.computed_daily.keys())
    new_daily = {
        dt: point.total for dt, point in after.computed_daily.items() if dt not in before_dates
    }

    # daily_close is counts-only (too many rows for tuple diff)
    daily_close_delta = max(0, after.daily_close_count - before.daily_close_count)

    # Net worth = total + liabilities (matches frontend's ``netWorth``).
    # ``point.total`` alone is gross assets and historically was labelled
    # "Net Worth" in the email despite excluding liabilities — tightening
    # that here aligns the number with how the frontend displays it.
    point_before, nw_before_date = _latest_entry(before.computed_daily)
    point_after, nw_after_date = _latest_entry(after.computed_daily)
    nw_before = point_before.net_worth if point_before is not None else None
    nw_after = point_after.net_worth if point_after is not None else None
    nw_delta: float | None = (
        nw_after - nw_before if (nw_before is not None and nw_after is not None) else None
    )

    # FRED: fire only on *set* changes — added or removed indicators. Normal
    # runs have a stable key set so this will be False; True only when the
    # pipeline adds a new series or one is retired. Avoids the "FRED: 9
    # indicator(s) refreshed" noise on every successful run.
    econ_keys_added = sorted(after.econ_series_keys - before.econ_series_keys)
    econ_keys_removed = sorted(before.econ_series_keys - after.econ_series_keys)
    econ_refreshed = bool(econ_keys_added or econ_keys_removed)

    emp_before = before.empower_latest_value
    emp_after = after.empower_latest_value
    emp_delta = (
        emp_after - emp_before if emp_before is not None and emp_after is not None else None
    )

    return SyncChangelog(
        fidelity_added=fidelity_added,
        qianji_added_count=qianji_added_count,
        qianji_added_by_category=qianji_by_cat,
        qianji_added_rows_by_category=qianji_rows_by_cat,
        computed_daily_added=new_daily,
        daily_close_added=daily_close_delta,
        daily_close_max_after=after.daily_close_max_date,
        econ_refreshed=econ_refreshed,
        econ_keys_added=econ_keys_added,
        econ_keys_removed=econ_keys_removed,
        empower_added=max(0, after.empower_snapshots_count - before.empower_snapshots_count),
        empower_value_delta=emp_delta,
        empower_value_before=emp_before,
        empower_value_after=emp_after,
        net_worth_before=nw_before,
        net_worth_after=nw_after,
        net_worth_delta=nw_delta,
        net_worth_point_before=point_before,
        net_worth_point_after=point_after,
        net_worth_before_date=nw_before_date,
        net_worth_after_date=nw_after_date,
    )


def _latest_entry(daily: dict[str, NetWorthPoint]) -> tuple[NetWorthPoint | None, str | None]:
    """Return (point, date) for the max date. ``(None, None)`` if the dict is empty."""
    if not daily:
        return (None, None)
    latest_date = max(daily.keys())
    return (daily[latest_date], latest_date)
