"""Shared Qianji constants — platform DB paths, user timezone, type codes.

Kept as a small leaf module so both :mod:`.ingest` and :mod:`.balances`
can import from it without creating a cycle. Everything here is a
module-level constant; no functions.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from ..types import QJ_EXPENSE, QJ_INCOME, QJ_REPAYMENT, QJ_TRANSFER

_MAC_DB_PATH = Path.home() / "Library/Containers/com.mutangtech.qianji.fltios/Data/Documents/qianjiapp.db"
_WIN_DB_PATH = Path(os.environ.get("APPDATA", "")) / "com.mutangtech.qianji.win/qianji_flutter/qianjiapp.db"

# ``QIANJI_DB_PATH_OVERRIDE`` lets L2 regression tests point the build at a
# fixture DB without touching the caller's home directory / %APPDATA%. Unset
# in production; real builds keep the per-platform default.
_OVERRIDE_PATH = os.environ.get("QIANJI_DB_PATH_OVERRIDE")
if _OVERRIDE_PATH:
    DEFAULT_DB_PATH = Path(_OVERRIDE_PATH)
else:
    DEFAULT_DB_PATH = _WIN_DB_PATH if sys.platform == "win32" else _MAC_DB_PATH

# Qianji type codes → internal type names
_TYPE_MAP = {0: QJ_EXPENSE, 1: QJ_INCOME, 2: QJ_TRANSFER, 3: QJ_REPAYMENT}

_BASE_CURRENCY = "USD"
# Minimum difference between base-currency and source-currency amounts to consider
# a real conversion (filters out unconverted records where bv == sv).
_CONVERSION_TOLERANCE = 0.01

# Qianji stores each bill's ``time`` as a Unix epoch captured at the moment
# the user taps save — the timestamp itself is timezone-agnostic, but which
# *day* we attribute it to depends on the user's wall-clock. Truncating in
# UTC is almost never right: for a user on the US West Coast, 39% of bills
# (everything logged after ~16:00 local) get attributed to the following
# UTC day — systematically mis-dating daily cashflow by one day.
#
# ``QIANJI_USER_TZ`` lets callers pin a different zone for tests / fixtures.
# Default is the zone the user actually lives in (PT); the L2 regression
# fixture overrides to UTC to keep the golden deterministic.
_USER_TZ = ZoneInfo(os.environ.get("QIANJI_USER_TZ", "America/Los_Angeles"))
