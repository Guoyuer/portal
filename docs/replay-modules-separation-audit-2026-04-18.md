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
