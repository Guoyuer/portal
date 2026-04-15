"""FidelitySource — owns all Fidelity-specific logic.

Owns:
  - CSV parsing delegated to :mod:`etl.ingest.fidelity_history` (absorbed
    into this module in Task 16 of the data-source abstraction refactor).
  - Per-day position + cash query over a pre-ingested
    ``fidelity_transactions`` table, returning a unified
    ``list[PositionRow]`` that ``compute_daily_allocation`` treats
    identically to every other investment source.
  - T-Bill CUSIP aggregation (8+ digit symbols surface as ``T-Bills``
    with value = face quantity).
  - Mutual-fund T-1 price dating (yfinance stamps MF NAV with the wrong
    date; we look up T-1 instead).
  - Per-account cash → money-market-fund ticker routing
    (``fidelity_accounts[account_number]``, defaulting to ``FZFXX``).

``positions_at`` delegates transaction replay to the legacy
:func:`etl.timemachine.replay_from_db`. The source-agnostic
:func:`etl.replay.replay_transactions` primitive understands a narrower
action alphabet (BUY / SELL / REINVESTMENT only) than Fidelity's
transaction stream, which also includes REDEMPTION PAYOUT, TRANSFERRED
FROM/TO, DISTRIBUTION, and EXCHANGED TO — all position-affecting actions
that ``_replay_core`` handles via ``POSITION_PREFIXES``. Switching
Fidelity to the narrower primitive would change the share-count output
for real data; the migration to that primitive is a separate,
behaviour-preserving refactor.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import ClassVar

import pandas as pd

from etl.sources import (
    _REGISTRY,
    InvestmentSource,
    PositionRow,
    PriceContext,
    SourceKind,
)

# Default set of mutual-fund tickers that need T-1 price lookup. Mirrors the
# legacy ``allocation._MUTUAL_FUNDS`` constant so that behaviour stays
# unchanged when ``from_raw_config`` is called without an explicit
# ``mutual_funds`` key.
_DEFAULT_MUTUAL_FUNDS: frozenset[str] = frozenset({"FXAIX", "FSSNX", "FNJHX"})


@dataclass(frozen=True)
class FidelitySourceConfig:
    downloads_dir: Path
    fidelity_accounts: dict[str, str]  # account_number → money-market fund ticker
    mutual_funds: frozenset[str]
    table: str = "fidelity_transactions"


class FidelitySource:
    kind: ClassVar[SourceKind] = SourceKind.FIDELITY

    def __init__(self, config: FidelitySourceConfig, db_path: Path) -> None:
        self._config = config
        self._db_path = db_path

    @classmethod
    def from_raw_config(
        cls, raw: dict[str, object], db_path: Path
    ) -> FidelitySource:
        """Build a FidelitySource from the raw ``config.json`` shape.

        Reads ``fidelity_downloads`` (CSV directory), ``fidelity_accounts``
        (per-account MM fund mapping), and ``mutual_funds`` (T-1 price
        tickers). Missing keys fall back to sensible defaults that match
        the pre-refactor ``allocation.py`` behaviour.
        """
        downloads_raw = raw.get("fidelity_downloads")
        downloads_dir = Path(downloads_raw) if isinstance(downloads_raw, (str, Path)) else Path.home() / "Downloads"
        accounts_raw = raw.get("fidelity_accounts") or {}
        accounts = dict(accounts_raw) if isinstance(accounts_raw, dict) else {}
        mfs_raw = raw.get("mutual_funds")
        if mfs_raw is None:
            mutual_funds = _DEFAULT_MUTUAL_FUNDS
        elif isinstance(mfs_raw, (list, tuple, set, frozenset)):
            mutual_funds = frozenset(mfs_raw)
        else:
            mutual_funds = _DEFAULT_MUTUAL_FUNDS
        return cls(
            FidelitySourceConfig(
                downloads_dir=downloads_dir,
                fidelity_accounts=accounts,
                mutual_funds=mutual_funds,
            ),
            db_path,
        )

    def ingest(self) -> None:
        """Scan downloads_dir for ``Accounts_History*.csv`` and ingest each file.

        Thin wrapper around :func:`etl.ingest.fidelity_history.ingest_fidelity_csv`.
        Task 16 inlines the CSV-parsing body into this module and deletes the
        legacy file.
        """
        from etl.ingest.fidelity_history import ingest_fidelity_csv  # temporary thunk
        for csv_path in sorted(self._config.downloads_dir.glob("Accounts_History*.csv")):
            ingest_fidelity_csv(self._db_path, csv_path)

    def positions_at(self, as_of: date, prices: PriceContext) -> list[PositionRow]:
        """Return one PositionRow per (account, ticker) position + cash bucket.

        Reuses :func:`etl.timemachine.replay_from_db` for the core
        cost-basis accumulator. That function understands the full Fidelity
        action alphabet (BUY / SELL / REINVESTMENT plus REDEMPTION PAYOUT,
        TRANSFERRED FROM/TO, DISTRIBUTION, EXCHANGED TO) and correctly
        excludes money-market symbols from position accumulation. The
        narrower :func:`etl.replay.replay_transactions` primitive is not
        yet sufficient; migrating to it is a separate refactor.
        """
        from etl.timemachine import replay_from_db

        result = replay_from_db(self._db_path, as_of)
        positions: dict[tuple[str, str], float] = result["positions"]
        cash_by_account: dict[str, float] = result["cash"]
        cost_basis: dict[tuple[str, str], float] = result.get("cost_basis") or {}

        rows: list[PositionRow] = []

        # ── Positions (one row per (account, ticker); may emit multiple
        # ── PositionRows with the same ``ticker`` when the same symbol is
        # ── held in more than one account — the caller aggregates by ticker).
        for (acct, sym), qty in positions.items():
            cb = cost_basis.get((acct, sym))

            if sym and sym[0].isdigit() and len(sym) >= 8:
                # T-Bill CUSIP: face value quantity, bucketed under "T-Bills".
                rows.append(PositionRow(
                    ticker="T-Bills",
                    value_usd=qty,
                    quantity=qty,
                    cost_basis_usd=cb,
                    account=acct,
                ))
                continue

            # Regular symbol: price-lookup against PriceContext.
            p_date = prices.mf_price_date if sym in self._config.mutual_funds else prices.price_date
            if sym in prices.prices.columns and p_date in prices.prices.index:
                price = prices.prices.loc[p_date, sym]
                if pd.notna(price):
                    rows.append(PositionRow(
                        ticker=sym,
                        value_usd=qty * float(price),
                        quantity=qty,
                        cost_basis_usd=cb,
                        account=acct,
                    ))
            # Missing price: skip silently — matches legacy
            # ``_add_fidelity_positions`` behaviour (logs a warning but
            # excludes the holding; log line stays with the caller).

        # ── Per-account cash routed to each account's MM fund.
        for acct, bal in cash_by_account.items():
            mm_ticker = self._config.fidelity_accounts.get(acct, "FZFXX")
            rows.append(PositionRow(
                ticker=mm_ticker,
                value_usd=bal,
                account=acct,
            ))

        return rows


# Register this class in the central registry at import time.
if FidelitySource not in _REGISTRY:
    _REGISTRY.append(FidelitySource)


_: type[InvestmentSource] = FidelitySource  # structural-subtype sanity check
