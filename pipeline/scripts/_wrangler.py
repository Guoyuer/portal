"""Shared wrangler CLI helpers for nightly D1 sync scripts.

Both ``sync_prices_nightly.py`` and ``project_networth_nightly.py`` shell out
to ``npx wrangler d1 execute`` to query/mutate the remote D1 database from
GitHub Actions. The three routines here consolidate that:

- :func:`run_wrangler_query` — run a SELECT via ``--json --command`` and
  extract the result rows from wrangler's banner-prefixed stdout.
- :func:`run_wrangler_exec_file` — apply a SQL file via ``--file``.
- :func:`sql_escape` — inline-escape a Python scalar for SQL ``VALUES (...)``.

The caller must supply ``CLOUDFLARE_API_TOKEN`` + ``CLOUDFLARE_ACCOUNT_ID``
in the environment (wrangler reads them directly) and have Node/npm on PATH.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

# Resolve worker/ relative to this file so callers don't have to rediscover it.
_PROJECT_DIR = Path(__file__).resolve().parent.parent
_WORKER_DIR = _PROJECT_DIR.parent / "worker"


def run_wrangler_query(sql: str, db_name: str = "portal-db") -> list[dict[str, Any]]:
    """Run a SELECT against remote D1 via ``wrangler --json`` and parse the result.

    Wrangler emits a human-readable banner before the JSON payload; we locate
    the first ``[`` and parse from there. Empty result sets return ``[]``.
    Raises ``RuntimeError`` if no JSON array is found in stdout.
    """
    cmd = [
        "npx", "wrangler", "d1", "execute", db_name,
        "--remote", "--json", "--command", sql,
    ]
    result = subprocess.run(
        cmd, cwd=str(_WORKER_DIR), capture_output=True, text=True, check=True
    )
    idx = result.stdout.find("[")
    if idx < 0:
        raise RuntimeError(f"No JSON array in wrangler output:\n{result.stdout}")
    payload = json.loads(result.stdout[idx:])
    if not payload:
        return []
    return payload[0].get("results", []) or []


def run_wrangler_exec_file(sql_path: Path, db_name: str = "portal-db") -> None:
    """Apply a SQL file against remote D1 via ``wrangler --file``."""
    cmd = [
        "npx", "wrangler", "d1", "execute", db_name,
        "--remote", "--file", str(sql_path),
    ]
    subprocess.run(cmd, cwd=str(_WORKER_DIR), check=True)


def sql_escape(value: object) -> str:
    """Escape a Python scalar for inline SQL ``VALUES`` / ``WHERE`` use."""
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"
