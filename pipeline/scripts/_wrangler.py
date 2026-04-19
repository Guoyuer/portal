"""Shared wrangler CLI helpers for D1 sync scripts.

Every caller that shells out to ``npx wrangler d1 execute`` routes through
one of four helpers: :func:`run_wrangler_query` (SELECT via ``--json``),
:func:`run_wrangler_exec_file` (apply a ``--file``), :func:`run_wrangler_command`
(one-off SQL via ``--command`` for ALTERs / single INSERTs), and
:func:`sql_escape` (Python scalar → SQL literal).

All subprocess helpers resolve ``npx`` via :func:`shutil.which` — on
Windows ``npx`` alone isn't executable without ``shell=True``, so we pass
``npx.cmd``'s full path directly. ``local=True`` flips ``--remote`` →
``--local`` for wrangler-dev. Caller supplies ``CLOUDFLARE_API_TOKEN`` +
``CLOUDFLARE_ACCOUNT_ID`` in the environment (wrangler reads them).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

_PROJECT_DIR = Path(__file__).resolve().parent.parent
_WORKER_DIR = _PROJECT_DIR.parent / "worker"


def _resolve_npx() -> str:
    npx = shutil.which("npx")
    if npx is None:
        msg = "npx not found in PATH — install Node.js or add npm bin to PATH"
        raise RuntimeError(msg)
    return npx


def _remote_flag(local: bool) -> str:
    return "--local" if local else "--remote"


def _fail(kind: str, result: subprocess.CompletedProcess[str], detail: str) -> None:
    """Normalized RuntimeError for any failed wrangler invocation."""
    raise RuntimeError(
        f"wrangler {kind} failed (rc={result.returncode})\n{detail}\n"
        f"stderr:\n{result.stderr or '(empty)'}\n"
        f"stdout:\n{result.stdout or '(empty)'}"
    )


def run_wrangler_query(
    sql: str, *, local: bool = False, db_name: str = "portal-db",
) -> list[dict[str, Any]]:
    """Run a SELECT against D1 via ``wrangler --json`` and parse the result.

    Wrangler emits a banner before the JSON payload; we locate the first
    ``[`` and parse from there. Empty result sets return ``[]``.
    """
    cmd = [
        _resolve_npx(), "wrangler", "d1", "execute", db_name,
        _remote_flag(local), "--json", "--command", sql,
    ]
    result = subprocess.run(cmd, cwd=str(_WORKER_DIR), capture_output=True, text=True)
    if result.returncode != 0:
        _fail("query", result, f"SQL: {sql}")
    idx = result.stdout.find("[")
    if idx < 0:
        raise RuntimeError(f"No JSON array in wrangler output:\n{result.stdout}")
    payload = json.loads(result.stdout[idx:])
    if not payload:
        return []
    return payload[0].get("results", []) or []


def run_wrangler_exec_file(
    sql_path: Path, *, local: bool = False, db_name: str = "portal-db",
) -> None:
    """Apply a SQL file against D1 via ``wrangler --file``.

    Prints wrangler's stdout on success so operators see the summary.
    """
    cmd = [
        _resolve_npx(), "wrangler", "d1", "execute", db_name,
        _remote_flag(local), f"--file={sql_path}",
    ]
    result = subprocess.run(cmd, cwd=str(_WORKER_DIR), capture_output=True, text=True)
    if result.returncode != 0:
        _fail("--file", result, f"File: {sql_path}")
    if result.stdout:
        print(result.stdout, end="")


def run_wrangler_command(
    sql: str, *, local: bool = False, db_name: str = "portal-db",
) -> None:
    """Execute a single SQL string against D1 via ``wrangler --command``.

    Used for one-shot DDL (ALTERs) and single audit-log INSERTs where
    batching through a temp file would be overkill.
    """
    cmd = [
        _resolve_npx(), "wrangler", "d1", "execute", db_name,
        _remote_flag(local), f"--command={sql}",
    ]
    result = subprocess.run(cmd, cwd=str(_WORKER_DIR), capture_output=True, text=True)
    if result.returncode != 0:
        _fail("command", result, f"SQL: {sql}")


def sql_escape(value: object) -> str:
    """Escape a Python scalar for inline SQL ``VALUES`` / ``WHERE`` use."""
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"
