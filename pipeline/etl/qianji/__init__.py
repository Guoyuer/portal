"""Read Qianji data directly from the local SQLite database.

Platform-specific default paths:
- macOS: ~/Library/Containers/com.mutangtech.qianji.fltios/Data/Documents/qianjiapp.db
- Windows: %APPDATA%/com.mutangtech.qianji.win/qianji_flutter/qianjiapp.db

This is more reliable than CSV export:
- Always up-to-date (synced by the app)
- No manual export needed
- Includes accurate account balances (user_asset.money)
- Includes all transactions (user_bill)

Submodules:

- :mod:`.config` — platform DB paths, user timezone, type codes.
- :mod:`.currency` — ``extra.curr`` decoding + USD/CNY conversion
  (:func:`parse_qj_amount`, :func:`parse_qj_target_amount`).
- :mod:`.ingest` — read bills from the source DB, write
  ``qianji_transactions`` (:func:`load_all_from_db`,
  :func:`ingest_qianji_transactions`).
- :mod:`.balances` — point-in-time reverse-replay
  (:class:`QianjiSnapshot`, :func:`qianji_balances_at`).
"""

from __future__ import annotations

from .balances import QianjiSnapshot, qianji_balances_at
from .config import DEFAULT_DB_PATH
from .currency import parse_qj_amount, parse_qj_target_amount
from .ingest import ingest_qianji_transactions, load_all_from_db

__all__ = [
    "DEFAULT_DB_PATH",
    "QianjiSnapshot",
    "ingest_qianji_transactions",
    "load_all_from_db",
    "parse_qj_amount",
    "parse_qj_target_amount",
    "qianji_balances_at",
]
