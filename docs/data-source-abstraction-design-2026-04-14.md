# Data Source Abstraction — Design

**Date:** 2026-04-14
**Status:** Draft (pending implementation plan)
**Scope:** B (query Protocol + registry + shared transaction-replay + Robinhood persistence)
**Constraint:** Zero observable pipeline regression.

## Context

The Python pipeline ingests 4 investment sources (Fidelity, Empower 401k, Robinhood, Qianji) and 2 market-data sources (Yahoo, FRED). No abstraction: each source has its own reader, parser, DB writer, and a copy-pasted `_add_*` helper in `etl/allocation.py`. Recent bug history includes ordering errors (Fidelity `ORDER BY id` instead of date), type drift between shared TypedDicts, and Robinhood's divergent on-the-fly replay that doesn't use the same cost-basis primitive as Fidelity.

Adding a 5th source today = ~500 LOC across 6 files, ~80% copy-paste.

Goal: unify the **query layer** across investment sources and extract the shared **transaction-replay** primitive, so adding a 5th broker costs ~100 LOC and a new parser — nothing else.

## Non-goals

- Unifying the **ingest layer** (CSV / QFX / foreign SQLite / broker-specific fields stay heterogeneous — forcing one `ingest(path)` signature produces a bag of optional methods).
- Pulling Qianji into the investment hierarchy (cash + spending has different semantics; no second cash source is planned).
- Pulling market data (Yahoo/FRED) into this abstraction (they produce series, not positions).
- Changing Worker behavior — it stays `SELECT → JSON`.
- Any dual-path / backcompat shim. Refactor is a clean cut; dead `_add_*` helpers get deleted.
- **Designing a shared `CsvTransactionBroker` ABC before migrating concrete sources.** Abstractions emerge from code, not imposed on it. The ABC gets extracted in Step 8, *after* Fidelity and Robinhood are both living in `etl/sources/`, and only if their actual overlap meets a threshold (see Step 8).

## Architecture principles

1. **All source-specific logic lives in `etl/sources/<name>.py`** — including CSV/QFX parsing, config-key mapping from `RawConfig`, per-source special rules (Fidelity's T-Bill CUSIP aggregation, MM-fund cash routing, mutual-fund T-1 dating), and the source's table name. **After this refactor, `compute_daily_allocation` contains zero references to any source's name.**
2. **Each source self-materializes from `RawConfig`** via a `from_raw_config(raw, db_path)` classmethod. The central registry is a list of classes only — no source-specific knowledge leaks into `build_investment_sources`.
3. **The shared replay primitive is source-agnostic** — `replay.py` contains no `SourceKind` branching and no knowledge of which sources are transaction-level. It takes a table name and date.
4. **Qianji and market data are outside the Protocol** and keep their own modules — this is not a leak of abstraction, it is a recognition that they have genuinely different shapes.

## Architecture

### `InvestmentSource` Protocol

```python
# etl/sources/__init__.py
from enum import StrEnum
from typing import ClassVar, Protocol

class SourceKind(StrEnum):
    FIDELITY = "fidelity"
    ROBINHOOD = "robinhood"
    EMPOWER = "empower"

@dataclass(frozen=True)
class PriceContext:
    """Passed uniformly to every InvestmentSource.positions_at.
    Sources that don't need prices (Empower uses pre-computed daily values) simply ignore this argument."""
    prices: pd.DataFrame     # columns: tickers, index: dates, values: close price
    price_date: date         # most recent trading day ≤ as_of (pre-resolved by caller)
    mf_price_date: date      # T-1 trading day for mutual funds (corrects yfinance's NAV dating)

@dataclass(frozen=True)
class PositionRow:
    ticker: str
    value_usd: float                     # always — the consumer-visible value
    quantity: float | None = None        # filled when the source tracks shares
    cost_basis_usd: float | None = None  # Robinhood surfaces this today; Fidelity adds it (internally tracked)
    account: str | None = None           # for account-aware routing (e.g., Fidelity MM fund per account)

class InvestmentSource(Protocol):
    """Protocol for all investment sources. Each concrete source holds its own typed config and
    db_path via __init__; methods take only the per-call varying arguments."""
    kind: ClassVar[SourceKind]           # class-level identifier; StrEnum so `kind == "fidelity"` still holds
    def ingest(self) -> None: ...
    def positions_at(self, as_of: date, prices: PriceContext) -> list[PositionRow]: ...
```

**Per-source config dataclasses** (frozen; one per source; hold everything that's fixed for a run):

```python
@dataclass(frozen=True)
class FidelitySourceConfig:
    downloads_dir: Path
    fidelity_accounts: dict[str, str]     # account number → money-market fund ticker
    mutual_funds: frozenset[str]          # tickers that need T-1 price dating
    table: str = "fidelity_transactions"

@dataclass(frozen=True)
class RobinhoodSourceConfig:
    csv_path: Path
    table: str = "robinhood_transactions"

@dataclass(frozen=True)
class EmpowerSourceConfig:
    downloads_dir: Path
    cusip_map: dict[str, str]              # CUSIP → fund ticker
```

**Concrete source constructors** take their own config + `db_path`:

```python
class FidelitySource:
    kind: ClassVar[SourceKind] = SourceKind.FIDELITY

    def __init__(self, config: FidelitySourceConfig, db_path: Path):
        self._config = config
        self._db_path = db_path

    def ingest(self) -> None: ...                                         # uses self._config.downloads_dir, self._db_path
    def positions_at(self, as_of: date, prices: PriceContext) -> list[PositionRow]:
        states = replay_transactions(self._db_path, self._config.table, as_of)
        ...
```

**Config slicing lives inside each source** via a `from_raw_config` classmethod. The central registry holds only class references — no `RawConfig` key names, no source-specific wiring:

```python
# etl/sources/fidelity.py
class FidelitySource(...):
    @classmethod
    def from_raw_config(cls, raw: RawConfig, db_path: Path) -> FidelitySource:
        return cls(
            FidelitySourceConfig(
                downloads_dir=raw["fidelity_downloads"],
                fidelity_accounts=raw["fidelity_accounts"],
                mutual_funds=frozenset(raw["mutual_funds"]),
            ),
            db_path,
        )

# etl/sources/__init__.py
_REGISTRY: list[type[InvestmentSource]] = [
    FidelitySource,
    RobinhoodSource,
    EmpowerSource,
]

def build_investment_sources(raw: RawConfig, db_path: Path) -> list[InvestmentSource]:
    return [cls.from_raw_config(raw, db_path) for cls in _REGISTRY]
```

This enforces Architecture Principle #2: `build_investment_sources` knows *nothing* about which `RawConfig` keys each source reads. Adding a 5th source changes this file by exactly one line (the `_REGISTRY` append).

**Per-source field invariants** (not expressible in the Protocol type; enforced by unit tests per source):
- `FidelitySource` MUST produce `cost_basis_usd` (internally tracked by the replay accumulator anyway — surfacing it is free and maintains symmetry).
- `RobinhoodSource` MUST produce `cost_basis_usd` (today's `_add_robinhood` returns a cost-basis dict; dropping it is a regression).
- `EmpowerSource` MAY leave `cost_basis_usd=None` (QFX snapshots don't carry it).
- `quantity`: Fidelity/Robinhood MUST fill it; Empower MAY leave None if the upstream `k401_daily` pipeline only retained dollar values.
- `account`: filled when the source tracks per-account data (Fidelity cash routing needs it); otherwise None.

- `ingest` stays heterogeneous per source (reads its own input format, writes its own tables).
- `positions_at` is the unified query — replaces `_add_fidelity_positions` / `_add_fidelity_cash` / `_add_robinhood` / `_add_401k` in `allocation.py`.
- Internal implementations stay heterogeneous: Fidelity / Robinhood replay transactions and apply prices internally, Empower queries its pre-computed daily snapshot table.
- **Protocol-shape guarantee by regression, not by design.** PositionRow's field set covers every output the current `_add_*` helpers produce *that's known to be consumed downstream* (value per ticker, cost basis for Robinhood, account for cash routing). If migration reveals an additional consumed field, the strict L1/L2/L3 regression catches it immediately and the field is added. The Protocol is allowed to grow during migration; observable behavior is not.
- **Field rationale:**
  - `value_usd` is required — every `_add_*` helper writes this into `ticker_values`.
  - `quantity` is nullable — Fidelity/Robinhood fill it; Empower may or may not, depending on what `k401_daily` preserves.
  - `cost_basis_usd` is nullable but must be produced by Robinhood (today's `_add_robinhood` returns cost-basis; dropping it is a regression) and by Fidelity (internally tracked anyway, costs nothing to surface, symmetry with Robinhood).
  - `account` enables Fidelity's cash-to-MM-fund routing (`fidelity_accounts` mapping) inside the source; callers no longer need to know the mapping.

### Shared replay primitive

Extract a new module `etl/replay.py`:

```python
@dataclass(frozen=True)
class PositionState:
    """What the cost-basis accumulator yields per ticker at a given as_of date."""
    quantity: float
    cost_basis_usd: float

def replay_transactions(
    db_path: Path,
    table: str,      # passed by the source via its own ClassVar; primitive stays source-agnostic
    as_of: date,
) -> dict[str, PositionState]:
    """Cost-basis accumulator over a standardized transactions table.
    Ticker-agnostic; source-specific normalizations (T-Bill CUSIP, etc.) stay in the caller."""
```

**Primitive has zero knowledge of `SourceKind`.** It takes a table name string and assumes a standardized column layout. Source-specific dispatch (which sources are transaction-level, which table each uses) lives in each source class's `ClassVar[str] _TABLE`. Adding a 5th transaction-level broker requires zero changes to `replay.py`.

- Fidelity's `replay_from_db()` (currently in `etl/timemachine.py`) delegates here.
- Robinhood's ad-hoc `replay_robinhood()` (currently in `etl/ingest/robinhood_history.py`) delegates here.
- Primitive requires a standardized column set on the table (date, action, ticker, quantity, amount_usd, ...); each source maps its input rows to this shape at ingest time.
- Connection lifecycle fully encapsulated — takes `db_path`, not a `sqlite3.Connection`. Matches the existing `replay_from_db` signature.

### Sources

Module layout:

```
etl/sources/
  __init__.py       # Protocol, PositionRow, build_investment_sources()
  fidelity.py       # FidelitySource
  robinhood.py      # RobinhoodSource
  empower.py        # EmpowerSource
etl/replay.py       # shared replay primitive
```

- `FidelitySource` — absorbs `etl/ingest/fidelity_history.py` + Fidelity-specific parts of `etl/timemachine.py`. Keeps T-Bill CUSIP handling as internal logic, not in the shared primitive.
- `RobinhoodSource` — absorbs `etl/ingest/robinhood_history.py`. **Changes from today: persisted to a new `robinhood_transactions` table (schema-aligned with `fidelity_transactions`).** The current on-the-fly CSV replay is deleted. `AllocationSources.rh_replay_fn` is removed.
- `EmpowerSource` — absorbs `etl/ingest/empower_401k.py` + snapshot-lookup logic currently in `etl/allocation.py::_add_401k_values`. Tables (`empower_snapshots`, `empower_funds`, `empower_contributions`) unchanged.

### Registry

In `etl/sources/__init__.py`:

```python
def build_investment_sources(config: RawConfig) -> list[InvestmentSource]:
    return [
        FidelitySource(config),
        RobinhoodSource(config),
        EmpowerSource(config),
    ]
```

Callers build once per run:

- `scripts/build_timemachine_db.py`: `for src in sources: src.ingest(db_path)` replaces the hardcoded ingest sequence.
- `etl/allocation.py::compute_daily_allocation()`: `for src in sources: positions.extend(src.positions_at(db_path, day))` replaces the 4 `_add_*` helpers.

### Qianji stays outside

Qianji is not an investment source; it tracks cash balances and categorized spending. Keeps its current module (`etl/ingest/qianji_db.py`, `etl/timemachine.py::replay_qianji`) unchanged. `compute_daily_allocation()` calls it separately via its existing function — it is not a member of the `InvestmentSource` registry.

Rationale: Qianji's output shape (per-account cash balance + per-category spending) doesn't fit `PositionRow`. Forcing it through the Protocol would require optional methods and would leak the abstraction.

### Market data stays outside

Yahoo/FRED continue to fetch into `computed_market_indices` and `econ_series` via their existing modules. They don't produce positions and are already well-contained.

## Regression harness

Three tiers, each catching a different drift class. All three must pass before merge.

### L1 — Real-data row-level golden master (local, manual)

**Purpose:** catch any row-level change in the final computed tables against the user's actual data.

1. **Before refactor, on `main`**:
   - Run `scripts/build_timemachine_db.py` to convergence.
   - Export every row of `computed_daily` and `computed_daily_tickers` (ordered by PK) as canonical JSON (stable key order, floats as full-precision strings).
   - SHA256 the canonical JSON. Layout:
     ```
     pipeline/tests/regression/baseline/
       .gitignore                         # excludes *.json dumps (contain personal data)
       computed_daily.sha256              # committed
       computed_daily_tickers.sha256      # committed
       computed_daily.json                # gitignored
       computed_daily_tickers.json        # gitignored
     ```
   - Commit the `.sha256` files to the refactor branch. JSON dumps stay local.
2. **During refactor**: after each meaningful commit, re-run build → re-export → compute hash → diff against the committed `.sha256` files. **Any diff blocks the commit.**
3. **Before merge**: final run, hashes match. If an intentional behavior change is required (e.g., bug fix discovered during refactor), user explicitly approves and the baseline is regenerated with a separate commit that calls out the change.

**Excluded columns** (filtered before hashing): `created_at`, `ingested_at`, or any other non-semantic timestamp fields. The exclusion list is an explicit constant in the harness script so additions are reviewable.

### L2 — Synthetic fixture golden (pytest, CI)

**Purpose:** cross-machine reproducibility; catches drift in CI without requiring user data.

Committed to `pipeline/tests/fixtures/regression/`:

- Fidelity CSV — ~50 rows covering: buy, sell, dividend, reinvest, withdrawal, cash deposit, T-Bill CUSIP, stock split.
- Robinhood CSV — ~20 rows covering: buy, sell, dividend, the `($x.xx)` negative-amount format.
- Empower QFX — 2 snapshot files (early + late) with 2 funds; covers the "as_of between snapshots" lookup case and the "as_of before first snapshot" edge.
- Qianji SQLite — ~20 `user_bill` rows + 3 `user_asset` accounts across CNY/USD.
- Market-data stub — deterministic fixture prices injected via test seam (no network in CI).
- `golden.json` — `computed_daily` + `computed_daily_tickers` rows dumped as canonical JSON.

Pytest test `pipeline/tests/regression/test_pipeline_golden.py`:

- Builds a fresh `timemachine.db` from the fixture inputs.
- Diffs `computed_daily` + `computed_daily_tickers` against `golden.json`. Fails on any diff.
- Intentional behavior change: update `golden.json` in the same commit, PR body explains the change.

### L3 — `/timeline` JSON response hash (end-to-end)

**Purpose:** catch drift in D1 view definitions, Worker SQL, or any non-DB shape-work that sits between `computed_daily*` and the consumer.

Automated via a single entry point `pipeline/scripts/regression.sh`:

1. Run `python3 scripts/build_timemachine_db.py` to convergence.
2. Run L1 — dump `computed_daily` + `computed_daily_tickers` to canonical JSON, SHA256, compare against committed `.sha256` files.
3. Start `wrangler dev --local` in the background with the post-build DB. Poll `http://localhost:8787/timeline` until first 2xx (timeout 30s).
4. `curl -s http://localhost:8787/timeline` (no query params) → SHA256 → compare against `pipeline/tests/regression/baseline/timeline.sha256`.
5. Kill wrangler. Exit non-zero if any tier diffed.

Baseline capture: a sibling `pipeline/scripts/regression_baseline.sh` runs the same steps but writes instead of comparing — used once on `main` before the refactor starts, and again whenever an intentional behavior change requires a baseline refresh (user approves + PR explains).

**Where L3 runs:** the entry-point script is called locally before every meaningful commit. CI runs L2 only (no wrangler dependency; pytest-native). Pre-merge, the user runs the full script one final time against real data.

**Rationale for L3 on top of L1/L2:** Worker is a thin `SELECT → JSON` adapter today, but view definitions or column aliases can drift independently of `computed_*` tables. L3 is the only tier covering the consumer-visible contract end-to-end, and automating it means "forgot to run L3" cannot happen silently.

### Strictness

Row-level, no aggregation. Chosen explicitly over date-aggregated hashing — we want to catch any drift immediately, and the noise-column exclusion list is a small, reviewable cost.

## Migration sequence

Each step is one independently reviewable commit. L1 + L2 + L3 stay green through the entire sequence.

1. **Capture baselines.** Add L2 fixture + `golden.json` based on current behavior. User runs L1 + L3 capture scripts on `main`, commits the hash files. Refactor work begins on a branch from this point.
2. **Scaffold the Protocol + shared primitive.** Add `etl/sources/__init__.py` with `InvestmentSource` Protocol, `PositionRow`, `build_investment_sources()` stub (returns empty list). Add `etl/replay.py` with `replay_transactions()` primitive. No call sites change. Regressions green.
3. **Migrate Fidelity.** Move `etl/ingest/fidelity_history.py` logic into `etl/sources/fidelity.py::FidelitySource`. Point `replay_from_db()` at the shared primitive. Move `fidelity_accounts`-to-MM-fund routing (currently in `_add_fidelity_cash`) into the source — cash positions emit as `PositionRow(ticker=mm_ticker, account=acct, ...)`. Move T-Bill CUSIP aggregation (`_add_fidelity_positions` lines 152-155) into the source. Register `FidelitySource` in `build_investment_sources()`. In `compute_daily_allocation()`, replace `_add_fidelity_positions` + `_add_fidelity_cash` calls with a single registry iteration over Fidelity (other sources still via old helpers). Regressions green.
4. **Migrate Robinhood.** Create `RobinhoodSource` in `etl/sources/robinhood.py`. Add new `robinhood_transactions` table to `etl/db.py` (schema-aligned with `fidelity_transactions`). Rewrite ingest to persist. Delete the on-the-fly CSV replay path and `AllocationSources.rh_replay_fn`. Register. Regressions green.
5. **Migrate Empower.** Move `etl/ingest/empower_401k.py` + snapshot-lookup logic into `etl/sources/empower.py::EmpowerSource`. Register. Regressions green.
6. **Delete dead code.** Remove `_add_fidelity_positions`, `_add_401k_values`, `_add_robinhood`, `AllocationSources`'s Robinhood-specific fields, any now-unused helpers. Regressions green.
7. **Final cleanup.** Doc updates (`CLAUDE.md` architecture section). Regressions green.
8. **(Conditional) Extract `CsvTransactionBroker` ABC.** With Fidelity and Robinhood now both living in `etl/sources/` under the Protocol, measure actual code overlap in their `ingest` + `positions_at` bodies.
   - **If overlap ≥ 70%** (the common shape — glob-scan for CSVs, map columns by name, classify actions, write standardized rows, replay, project — is real): extract `CsvTransactionBroker` ABC with `COLUMN_MAP` / `ACTION_RULES` / `DATE_FORMAT` / `AMOUNT_NEGATIVE_FORMAT` as class-level declarations. Migrate Fidelity (with its T-Bill / MM-fund / T-1 quirks retained as method overrides — Option B) and Robinhood onto it. Empower stays a direct Protocol implementer.
   - **If overlap < 70%** (Fidelity's quirks dominate the method bodies, or Robinhood's shape is too different): skip the extraction. YAGNI — revisit only when a 3rd CSV broker is being added.
   - Regressions green either way. This is the one step that can legitimately be dropped if the evidence doesn't support it; all previous steps are mandatory.

## Risks & mitigations

- **R1: Robinhood's persistence change introduces a new DB table, which changes intermediate DB state.** The regression contract is defined on consumer-visible outputs (`computed_daily*` + `/timeline` JSON), not on the full DB — so adding a table is fine as long as the final computation and API output are identical.
- **R2: The shared replay primitive must handle Fidelity's T-Bill CUSIP special-case without becoming source-aware.** Mitigation: CUSIP-to-ticker mapping stays in `FidelitySource`'s internal pre-processing; the primitive sees only already-normalized ticker symbols.
- **R3: Empower snapshot-lookup has edge cases (as_of between snapshots, as_of before first snapshot, gaps).** Mitigation: the L2 fixture includes all three cases explicitly.
- **R4: L3 requires running `wrangler dev --local` — slow and manual.** Accepted — L3 runs at merge gates, not per-commit. L1 + L2 are the fast per-commit checks.
- **R5: `gen_zod.py` parity check may fail if `FidelitySource` changes the TypedDict layout.** Mitigation: `FidelitySource` internals do not touch `types.py`'s `FidelityTxn` / `AllocationRow` / `TickerDetail` shapes; the Protocol's `PositionRow` is additive and not exported to Zod.

## Acceptance criteria

- All three regression tiers pass at every merge commit.
- Adding a hypothetical 5th investment source touches only: one new variant in `SourceKind`, one new file in `etl/sources/`, one new table in `etl/db.py` (if transaction-level), one line in `_REGISTRY`. **No other files.** `compute_daily_allocation` / `build_investment_sources` / `replay.py` do not change.
- `compute_daily_allocation` contains zero references to any specific source name (no `fidelity`, `robinhood`, `empower` substrings in that function's body).
- If Step 8 fires: adding a CSV-based broker (e.g., Schwab) that matches the `CsvTransactionBroker` shape requires only class-level declarations (`COLUMN_MAP`, `ACTION_RULES`, `kind`, etc.) plus a `from_raw_config` classmethod — no parser code, no custom `ingest`/`positions_at` bodies.
- `_add_fidelity_positions`, `_add_robinhood`, `_add_401k_values` are deleted.
- `AllocationSources.rh_replay_fn` is deleted; Robinhood is persisted like Fidelity.
- `CLAUDE.md` architecture section reflects the new structure.

## Open questions

Deferred to the implementation plan (not design-blocking, all resolvable with code inspection during migration):

- Exact column set for the standardized `*_transactions` shape used by the shared replay primitive.
- Whether `PositionRow` needs additional fields beyond the current set (discovered by L1/L2/L3 failing during migration, then extended).
- Market-data stubbing seam for L2 (deterministic price injection without network).
- Where the `fidelity_accounts` config lives after the refactor (likely moves into `FidelitySource.__init__` via `RawConfig`).
- Whether 401k's current `k401_daily` pre-computation pipeline (elsewhere in the build) stays as-is or is absorbed into `EmpowerSource.ingest`.
