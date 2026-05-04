"""Per-account cash → money-market-fund ticker routing.

Fidelity's Accounts_History CSV tallies cash movements at the account level.
The allocation engine wants a ticker-level view, so each account's cash
balance is mapped to the account's configured money-market fund ticker.

This routing used to live inline in ``allocation._add_fidelity_cash`` in the
pre-Phase-3 world; after the class→module refactor it belongs with the rest
of the Fidelity logic. Missing accounts fall back to ``FZFXX`` — matches
the pre-refactor default.
"""
from __future__ import annotations

from etl.sources._types import PositionRow
from etl.types import RawConfig

DEFAULT_MM_TICKER = "FZFXX"


def accounts_map(config: RawConfig) -> dict[str, str]:
    """Return the ``account_number → MM ticker`` routing table from raw config.

    Missing keys fall through to an empty dict so every account picks up the
    :data:`DEFAULT_MM_TICKER` fallback.
    """
    return dict(config.get("fidelity_accounts") or {})


def cash_rows(cash_by_account: dict[str, float], accounts: dict[str, str]) -> list[PositionRow]:
    """Turn a ``{account: USD balance}`` map into per-MM-fund position rows.

    Each account's cash surfaces as a single :class:`PositionRow` with the
    account's configured MM-fund ticker (or :data:`DEFAULT_MM_TICKER` when
    the account is unmapped). Cash is always at face value; the allocation
    surface does not need per-account share accounting.
    """
    return [
        PositionRow(
            ticker=accounts.get(acct, DEFAULT_MM_TICKER),
            value_usd=bal,
        )
        for acct, bal in cash_by_account.items()
    ]
