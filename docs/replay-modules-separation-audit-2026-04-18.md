# Replay Modules Separation — Audit

**Date:** 2026-04-18
**Status:** Accepted — keep separate
**Scope:** `etl/timemachine.py` vs `etl/replay.py`

## Context

During a simplification sweep, I hypothesised that `etl/timemachine.py` (164 LOC) and `etl/replay.py` (206 LOC) might have overlap worth consolidating — both modules expose a function named `replay_*`, and on a naïve scan "two replay modules" reads like accidental forking. Closer reading shows they do **entirely different kinds of work** and share no code. This note records the finding so the same false consolidation target doesn't keep coming up in future audits.

## What each module actually does

### `etl/replay.py` — investment-position replay

- **Direction:** forward (time T₀ → T₁).
- **Input:** `fidelity_transactions` / `robinhood_transactions` table rows — each row a ``BUY`` / ``SELL`` / ``DIVIDEND`` / ... event with a `date`, `ticker`, `quantity`, `amount`.
- **Output:** `dict[(account, ticker), PositionState]` — running share count + cost-basis USD per ticker, as of a given date.
- **Algorithm:** iterate transactions in date order, apply each event's effect on `PositionState` (add shares on buy, split lots on sell, credit cash on dividend, etc.).
- **Consumers:** `FidelitySource.positions_at()` and `RobinhoodSource.positions_at()` — these feed daily allocation / holdings-detail computation.
- **Abstraction status:** already source-agnostic (works off generic columns passed in via `replay_transactions(..., date_col="...", ticker_col="...", amount_col="...", account_col="...")`). Any new broker that exposes transaction rows can reuse it.

### `etl/timemachine.py` — Qianji balance reverse-replay

- **Direction:** reverse (current snapshot → T_past).
- **Input:** Qianji app DB — `user_asset` (current balances) + `user_bill` (cashflow rows: `expense` / `income` / `transfer` / `repayment`).
- **Output:** `dict[account_name, float]` — per-account balance (in its native currency) at `as_of`.
- **Algorithm:** start from today's `user_asset.money` snapshot, walk bills in **reverse** date order, undo each bill's effect on the source/target account (add back spent money on expense, subtract received money on income, reverse the flow on transfers / repayments handling cross-currency `extra.curr.tv`).
- **Consumers:** `allocation.compute_daily_allocation` — uses reverse-replay to anchor historical safe-net totals on days before the full transaction history was cached.
- **Also contains:** `replay_qianji_currencies()` (returns each account's currency) and a standalone CLI `main()` for ad-hoc balance lookups at a date.

## Why they can't be consolidated

| Dimension | `replay.py` | `timemachine.py` |
|---|---|---|
| Time direction | forward | reverse |
| Source data | investment broker transactions (buy/sell/dividend) | personal-finance cashflow bills (expense/income/transfer) |
| Value tracked | share count + cost basis per ticker | currency balance per account |
| Data model | `PositionState` (shares, cost_basis_usd) | `float` in account's native currency |
| Cross-currency | N/A (equities are USD-native) | core concern (`extra.curr.tv`) |
| Starting point | zero (or a cached checkpoint) | current snapshot |
| Consumers | per-ticker allocation categorisation | per-day safe-net anchor |

Zero shared code paths. The only accidental overlap is the English word *replay* in both function names — but "replay" means "apply events in order" (forward) in the investment case and "undo events in reverse" in the Qianji case. Different semantics behind the same lexical root.

## Decision: keep separate

No consolidation target exists. Both modules are already minimal:

- `replay.py` is the source-agnostic investment engine — exactly the shared abstraction the 2026-04-14 data-source refactor extracted for a reason.
- `timemachine.py` owns Qianji's domain-specific reverse-replay logic (including the timezone-aware `_USER_TZ` + cross-currency `extra.curr.tv` handling) plus a tiny CLI.

Both docstrings already clearly delineate responsibility:

> "The Fidelity replay engine lives in `etl.replay` (`replay_transactions`). This module now only hosts the Qianji-side reverse-replay (`replay_qianji`) and the unified CLI that prints both sides at a given `as_of` date."
> — `etl/timemachine.py` module docstring

No action. This audit is the record that the comparison has been made.

## For future audits

If a future reviewer finds "two replay modules" suspicious, before proposing consolidation, check whether they solve different problems — "replay" is an overloaded term in finance/accounting (forward-apply txns vs reverse-apply cashflow). Here it's the latter.

If some *genuinely* shared primitive shows up later (e.g., a third kind of replay is introduced), the consolidation target is most likely a shared date-ordering helper or a shared "load events from a SQL table as a generator" utility — **not** merging the two existing modules.

---

## Deeper investigation — "都是状态机，为什么不能统一？"

The initial audit above ("zero shared code paths → keep separate") is correct but not the complete answer. A sharper counter-argument: **both replayers are state machines of the shape `S → [fold over sorted events] → S'`**, and the entire point of the state-machine abstraction is to be indifferent to what `S` and `E` concretely are. So "different state / different events / different handlers" isn't an argument against unification — it's what state machines normally differ by.

### What a unified abstraction would look like

```python
class Replayer(Protocol[S, E]):
    def initial(self, conn) -> S: ...
    def events(self, conn, until: date) -> Iterator[E]: ...
    def step(self, state: S, event: E) -> S: ...

def replay(db_path: Path, r: Replayer[S, E], until: date) -> S:
    with sqlite3.connect(str(db_path)) as conn:
        state = r.initial(conn)
        for event in r.events(conn, until):
            state = r.step(state, event)
        return state
```

Concrete replayers:

- `InvestmentReplayer`: `initial` = empty; `events` = `SELECT ... WHERE date_col <= as_of`; `step` = BUY/SELL/… dispatch.
- `QianjiReplayer`: `initial` = `user_asset` snapshot; `events` = `SELECT user_bill WHERE time > cutoff`; `step` = type 0/1/2/3 reverse-effect dispatch.

The "forward vs reverse" direction difference vanishes into each replayer's `events()` / `step()` implementation — it's not something the engine cares about.

### Why this still doesn't simplify the mental model **here**

State-machine frameworks earn their keep via features we don't use:

1. **Declarative transitions** (like XState, Stateless) — state diagram code, visualizable.
2. **Guards + side effects** — guards on edges, effects on entry/exit, making control flow legible.
3. **Introspection / debug tooling** — serializable traces, step-through replay from any snapshot.
4. **Exhaustiveness guarantees** — sum-type match checked by compiler.

We have **none of these needs**. What we would actually get is a `functools.reduce` with a renamed runner. And we'd pay for it:

- **Mutation cost**: `replay_transactions` relies on `defaultdict(float)` in-place updates. Pure `(S, E) → S` forces either dict-copy per event (slow over ~6k tx) or a "mutate-and-return-same-ref" convention that leaks the abstraction.
- **Trace cost**: reader goes from "one 150-LOC function" to "Protocol + runner + 2 concrete classes in 4 files." Each method smaller, total attention higher.
- **Typing overhead**: `Protocol[S, E]` + `TypeVar` + Generic methods under `mypy --strict` is verbose; `ReplayResult` vs `dict[str, float]` union doesn't compress cleanly.
- **LOC neutral**: 200 + 80 existing → ~260 unified. No reduction, +1 indirection.

**Python isn't the right language for gains #1–4**, and without those gains, "state-machine framework" is just `reduce` with ceremony.

### When introducing the framework would pay off

Clear triggers:

- **3rd replayer** (tax lot matching, cashflow forecast, second broker outside InvestmentSource). Two sites amortize nothing; three start to. One of our two sites is **already multi-source** via `replay_transactions` (Fidelity/Robinhood/Empower) — so the real site count is `{investment: 1 polymorphic, Qianji: 1}`, not 2 monolithic.
- **Event-sourcing / audit requirement** — per-transition trace, snapshot-resume, replay-from-checkpoint UI.
- **Bug correlation between replayers** — if the same class of bug keeps appearing in both modules, that's evidence of missing shared constraint.

None of these apply today.

---

## Improvement plan — what actually simplifies

The real gains come from **making the API speak the consumer's language and letting implementation vocabulary disappear**, not from adding abstraction layers.

Current pains, concrete:

1. `replay_transactions` has 12 parameters; each call site repeats 7–9 of them.
2. `replay_qianji_currencies` duplicates a `SELECT` from `user_asset` that `replay_qianji` already does.
3. `etl/timemachine.py` is a historical module — after the 2026-04-14 data-source refactor extracted `etl/replay.py`, what remains is Qianji-specific logic + a CLI + a re-exported path constant. The "timemachine" concept no longer maps to the module's contents.
4. `allocation.py:347–354` has a `needs_qj` / `last_qj_replay` / `qj_replayed` triple-flag dance — a **hand-rolled imperative cache** existing because reverse-replay is expensive.
5. Function names (`replay_*`) expose implementation (forward vs reverse fold), not intent ("give me X at date Y").

### Tier 1 — one PR, ~-80 LOC, zero behavior change

**A. `ReplayConfig` dataclass** — pack `replay_transactions`' 12 parameters into a per-source frozen dataclass declared once at module level.

```python
# etl/sources/fidelity/__init__.py
FIDELITY_REPLAY = ReplayConfig(
    table="fidelity_transactions",
    date_col="run_date", ticker_col="symbol", amount_col="amount",
    account_col="account_number",
    exclude_tickers=MM_SYMBOLS,
    track_cash=True, lot_type_col="lot_type",
    mm_drip_tickers=MM_SYMBOLS,
)

# Call site
result = positions_at(db, FIDELITY_REPLAY, as_of)
```

This captures ~80% of the "state machine unification" benefit **without** the Protocol / generic / inheritance cost. Each source is a frozen dataclass instance (a "replayer instance" without being a class). Adding a source = add a dataclass, one line at the call site.

**B. Rename to consumer semantics.**

| Current | New | Intent |
|---|---|---|
| `replay_transactions(...)` | `positions_at(db, config, as_of)` | "positions on date Y" |
| `replay_qianji(...)` | `qianji_balances_at(db, as_of)` | "Qianji balances on date Y" |
| `replay_qianji_currencies(...)` | fold into `qianji_balances_at` | — |

Reader stops asking "forward or reverse?" — not their concern. They asked for state at a date, they get state at a date.

**C. Merge `replay_qianji_currencies` into `qianji_balances_at`.**

```python
@dataclass(frozen=True)
class QianjiSnapshot:
    balances: dict[str, float]
    currencies: dict[str, str]

def qianji_balances_at(db_path: Path, as_of: date | None = None) -> QianjiSnapshot: ...
```

`allocation.py` setup calls once with `as_of=None` to get currencies (they don't change over time); loop only cares about `balances`. One duplicate `SELECT` removed.

**D. Delete `etl/timemachine.py::main()` CLI** (~-55 LOC).

Zero tests cover it. Zero automation invokes `python -m etl.timemachine`. The combined-CLI use case is covered by `scripts/inspect_qianji.py` (Qianji side) + `scripts/verify_positions.py` (Fidelity side).

**E. Import `DEFAULT_QJ_DB` directly from `etl.ingest.qianji_db`** in `build_timemachine_db.py`, not via `etl.timemachine` indirection. Clears the path for Tier 2.

**Tier 1 net**: call sites go from 10-kwarg blasts to 3 arguments; two function names speak the consumer's language; `replay_qianji_currencies` and the CLI disappear; ~-80 LOC.

### Tier 2 — follow-up PR, architectural alignment

**F. Dissolve `etl/timemachine.py`.** Move Qianji's `qianji_balances_at` into either:

- `etl/sources/qianji.py` (new module, matches other sources' layout), or
- `etl/ingest/qianji_db.py` (co-locate with the same-domain code).

Today's asymmetry:

- Fidelity's "state at date" → `etl/sources/fidelity/__init__.py`
- Robinhood's "state at date" → `etl/sources/robinhood.py`
- **Qianji's "state at date" → `etl/timemachine.py`** ← outlier

After: every source lives under `etl/sources/` (or its domain module) with a uniform `*_at(as_of)` function. `timemachine.py` — a residual historical name that no longer maps to module contents — is deleted. Reader no longer needs to remember "timemachine" as a term.

**G. Uniform `*_at` naming** across all three source modules. Qianji stays outside the `InvestmentSource` Protocol (return type differs — `QianjiSnapshot` vs `PositionRow`), but the **shape axis** (every source exposes `*_at(as_of)`) is aligned. `allocation.py` reader sees a flat symmetric layout.

### Tier 3 — deferred, bigger question

**H. Pre-compute daily Qianji snapshots at build time.**

Current pain (`etl/allocation.py:347-354`):

```python
needs_qj = not qj_replayed or any(
    d > (last_qj_replay or date.min) and d <= current for d in qj_txn_dates
)
if needs_qj:
    state.qj_balances = replay_qianji(qj_db, current)
    last_qj_replay = current
    qj_replayed = True
```

This is a hand-rolled imperative cache. It exists because reverse-replay is expensive and re-running it per day would be wasteful. Proposal: run `qianji_balances_at` once per date at build time, persist the result.

```sql
CREATE TABLE qianji_daily_balance (
    date TEXT, account TEXT, balance REAL, currency TEXT,
    PRIMARY KEY (date, account)
);
```

`allocation.py` becomes:

```python
balances_by_day = load_qianji_daily_balances(db, start, end)  # single SELECT
# needs_qj / last_qj_replay / ReplayState.qj_balances all deleted
```

Reverse-replay still exists — but only at build time, filling the table. In the query path, Qianji data flows like prices: stored, indexed, queried. Reader's mental model becomes "tables are authoritative, build fills them" — the imperative-cache wart disappears.

**Why not now**:
- Bigger blast radius (new table, new build step, incremental-rebuild policy).
- Regression gate + L1/L2/L3 tests cover correctness, but schema changes still need care.
- Current code is not buggy — just a bit ugly.

**Trigger**: if Qianji logic needs non-trivial debugging (where the reverse-replay + cache gating tangle matters), or a 3rd source wants the same per-day snapshot treatment, do this.

### Explicit non-goals

- **`Replayer[S, E]` Protocol abstraction**: Tier 1.A's dataclass approach captures ~80% of the benefit without Protocol / generic / inheritance overhead. In a 2-site world, the Protocol form is net-negative.
- **Generic `ordered_rows` context manager**: 3 sites × 3 lines saved = 9 LOC; below the "worth a public helper" threshold.
- **Merging `replay_transactions` and `qianji_balances_at` into one function**: different state spaces, mutation-heavy in one, forcing unification would cost more than it saves.

### Triggers to reconsider

Revisit this plan (specifically Tier 2/3 decisions) if:

1. **3rd replayer** appears (tax lot matching, cashflow forecast, non-InvestmentSource broker). Do Tier 3 and consider Protocol.
2. **Event-sourcing / audit-trail requirement** — need per-transition serializable traces or step-through debugging. A real FSM framework starts to pay off.
3. **Bug correlation** between `positions_at` and `qianji_balances_at` — same class of bug in both is evidence of a missing shared constraint worth factoring out.
4. **Debugging pain** around the `needs_qj` imperative cache in allocation — do Tier 3 first.

## Status

- Tier 1: planned, not yet implemented.
- Tier 2: queued after Tier 1 lands.
- Tier 3: deferred; reconsider at trigger.
