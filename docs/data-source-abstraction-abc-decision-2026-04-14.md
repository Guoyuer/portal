# ABC extraction decision (2026-04-14)

Measured overlap between `FidelitySource` and `RobinhoodSource` post-migration:
**~16-29%** (shared structural skeleton: ~28 lines; Fidelity SLOC: ~175;
Robinhood SLOC: ~95).

Threshold per spec (`docs/data-source-abstraction-design-2026-04-14.md`,
Phase 8 guidance): 70%. Decision: **skipped**.

## Method-by-method breakdown

### `ingest` / `_ingest_one_csv`

Shared skeleton (open-BOM CSV → `DictReader` → date-regex filter →
`parse_us_date` → classify action → append to tuple → range-replace
`DELETE BETWEEN ? AND ?` + `executemany INSERT`) accounts for roughly
20 lines.

Divergences:

- **CSV topology.** `FidelitySource` globs `Accounts_History*.csv`
  chronologically and loops `_ingest_one_csv` over each; Robinhood consumes
  a single configured `csv_path` (with an exists-guard no-op).
- **Preamble handling.** Fidelity scans for the `"Run Date"` header line and
  slices `lines[header_idx:]` before handing to `DictReader`. Robinhood's
  CSV has the header as line 1 — no preamble skip.
- **Amount grammar.** Robinhood's `_parse_amount` accepts `$1,234.56` *and*
  parenthesised negatives `($1,234.56)`; Fidelity uses plain
  `etl.types.parse_float` on a pre-signed `Amount` column.
- **Sign normalization.** Robinhood CSV emits SELL `Quantity` as positive
  and we negate at ingest to match the shared sign convention. Fidelity's
  raw CSV already encodes BUY/SELL signs correctly.
- **Action classification shape.** Fidelity has ~20 substring-regex rules
  (e.g. "YOU SOLD" → `ACT_SELL`, "EXCHANGED TO" → `ACT_EXCHANGE`) that fan
  into a two-level map (`ACT_*` legacy label → `ActionKind`). Robinhood
  has a flat 6-entry dict on the raw `Trans Code`, including the
  REC-as-BUY-with-zero-amount normalization.
- **Row schema.** Fidelity writes 13 columns (`run_date, account,
  account_number, action, action_type, action_kind, symbol, description,
  lot_type, quantity, price, amount, settlement_date`); Robinhood writes
  7 (`txn_date, action, action_kind, ticker, quantity, amount_usd,
  raw_description`). Column names and cardinalities differ; any shared
  `COLUMN_MAP` dataclass would need a verbose escape hatch for the
  Fidelity-only fields (account_number, price, settlement_date, lot_type).

### `positions_at`

Shared skeleton (iterate replayed states → look up price → emit `PositionRow`
or warn+skip on missing price) accounts for roughly 8 lines.

Divergences:

- **Replay primitive.** `FidelitySource` still delegates to the legacy
  `etl.timemachine.replay_from_db`, which understands a wider action
  alphabet (BUY / SELL / REINVESTMENT **plus** REDEMPTION PAYOUT,
  TRANSFERRED FROM/TO, DISTRIBUTION, EXCHANGED TO) via its
  `POSITION_PREFIXES` prefix match. `RobinhoodSource` uses the
  source-agnostic `etl.replay.replay_transactions` primitive.
- **State shape.** `replay_from_db` returns a dict keyed by
  `(account, symbol)` (Fidelity's per-account position dimension) plus a
  separate `cash_by_account` bucket plus a `cost_basis` dict; the primitive
  returns a dict keyed by `ticker` with `PositionState` values (qty +
  cost_basis only, no cash dimension).
- **Fidelity-only projection quirks.** T-Bill CUSIP aggregation (8+ digit
  symbols bucket under ticker `"T-Bills"` with `value_usd = qty`), mutual-
  fund T-1 price dating (`prices.mf_price_date` vs `prices.price_date`),
  and per-account cash → money-market ticker routing
  (`fidelity_accounts[acct]` with `FZFXX` default). Robinhood has none of
  these — it looks up `prices.price_date` directly and emits one
  `PositionRow` per ticker with no account field.

## Rationale

The two sources share about a fifth of their total SLOC — mostly the
boilerplate CSV-iteration skeleton and the range-replace INSERT pattern.
Beyond that they diverge on nearly every axis that would matter to an ABC:

- **Different replay primitives** → `positions_at` cannot be templated
  generically until Fidelity migrates off `replay_from_db`, which the
  spec (`fidelity.py` module docstring) calls out as a separate
  behaviour-preserving refactor.
- **Different position dimensions** → `(account, ticker)` vs `ticker`
  → any shared `_project` hook would need a union signature.
- **Different CSV schemas and ingestion topologies** → a shared `ingest`
  would need enough hooks (`_read_csv_files`, `_skip_preamble`,
  `_parse_amount`, `_post_parse_row`, `_build_row_tuple`, `_insert_sql`)
  that the "generic" implementation becomes a thin orchestrator over
  per-source overrides — more abstraction overhead than duplication
  removed.

The present concrete classes are easy to read top-to-bottom; each quirk
lives next to its adjacent logic. Forcing a shared base now would spread
Fidelity-only concerns across a base class + hook overrides and leave
Robinhood reading half its logic via `super()`. The Protocol-based
`InvestmentSource` structural subtype check
(`_: type[InvestmentSource] = FidelitySource` / `RobinhoodSource`) already
gives us the type safety benefit of an ABC without forcing inheritance.

Revisit when a third CSV-based broker (Schwab, Vanguard, etc.) is added —
a third concrete data point will either confirm the shared skeleton is
real or prove it's a coincidence of two. At that point we will also likely
have migrated Fidelity off `replay_from_db`, removing one of the major
divergences today.
