"""Sync local data to Cloudflare R2 for report generation.

Uploads Fidelity CSVs and Qianji DB to R2 bucket latest/ only when
files are new or changed since the last sync. Designed for macOS launchd.

Requires: wrangler CLI authenticated (`wrangler login`)

Usage:
    python scripts/sync.py                  # sync all
    python scripts/sync.py --db-only        # just Qianji DB
    python scripts/sync.py --force          # upload even if unchanged
    python scripts/sync.py --dry-run        # show what would be uploaded
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

# ── Platform-specific paths ──────────────────────────────────────────────────

_DOWNLOADS = Path.home() / "Downloads"

if sys.platform == "win32":
    import os

    _QIANJI_DB = Path(os.environ.get("APPDATA", "")) / "com.mutangtech.qianji.win/qianji_flutter/qianjiapp.db"
else:
    _QIANJI_DB = Path.home() / "Library/Containers/com.mutangtech.qianji.fltios/Data/Documents/qianjiapp.db"

_PROJECT_DIR = Path(__file__).resolve().parent.parent
_STATE_FILE = _PROJECT_DIR / ".sync_state.json"
_BUCKET = "asset-snapshot-data"

# ── State tracking (only upload changed files) ──────────────────────────────


def _file_hash(path: Path) -> str:
    """Fast hash of a file for change detection."""
    h = hashlib.md5()  # noqa: S324
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_state() -> dict[str, str]:
    if _STATE_FILE.exists():
        return json.loads(_STATE_FILE.read_text())
    return {}


def _save_state(state: dict[str, str]) -> None:
    _STATE_FILE.write_text(json.dumps(state, indent=2))


def _changed(path: Path, state: dict[str, str]) -> str | None:
    """Return the new hash if file changed since last sync, else None."""
    current = _file_hash(path)
    return current if state.get(path.name) != current else None


# ── File discovery ───────────────────────────────────────────────────────────


def _find_latest(directory: Path, prefix: str) -> Path | None:
    matches = sorted(directory.glob(f"{prefix}*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


# ── Upload via wrangler CLI ─────────────────────────────────────────────────


def _upload(local: Path, remote_name: str, *, dry_run: bool = False) -> bool:
    """Upload a file to R2 bucket latest/. Returns True if uploaded."""
    dest = f"latest/{remote_name}"
    if dry_run:
        print(f"  [dry-run] {local.name} -> r2://{_BUCKET}/{dest}")
        return True

    result = subprocess.run(
        ["wrangler", "r2", "object", "put", f"{_BUCKET}/{dest}", "--file", str(local)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  [error] {result.stderr.strip()}", file=sys.stderr)
        return False
    print(f"  {local.name} -> r2://{_BUCKET}/{dest}")
    return True


def _upload_meta(positions_path: Path | None, *, dry_run: bool = False) -> None:
    """Upload sync metadata for freshness tracking."""
    import tempfile

    meta = {
        "synced_at": datetime.now(tz=UTC).isoformat(),
        "positions_file": positions_path.name if positions_path else None,
    }
    if not dry_run:
        tmp = Path(tempfile.mktemp(suffix=".json"))
        tmp.write_text(json.dumps(meta, indent=2))
        _upload(tmp, "sync_meta.json")
        tmp.unlink()
    else:
        print(f"  [dry-run] sync_meta.json -> r2://{_BUCKET}/latest/sync_meta.json")


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync local data to Cloudflare R2")
    parser.add_argument("--db-only", action="store_true", help="Only sync Qianji DB")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be uploaded")
    parser.add_argument("--force", action="store_true", help="Upload even if unchanged")
    args = parser.parse_args()

    state = _load_state()
    uploaded = 0
    positions_path: Path | None = None
    now = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%S")

    print(f"Sync started: {now}")

    def _sync_file(path: Path, remote_name: str) -> None:
        nonlocal uploaded
        new_hash = _changed(path, state)
        if args.force or new_hash:
            if _upload(path, remote_name, dry_run=args.dry_run):
                state[path.name] = new_hash or _file_hash(path)
                uploaded += 1
        else:
            print(f"  [skip] {path.name} unchanged")

    # Qianji DB
    if _QIANJI_DB.exists():
        _sync_file(_QIANJI_DB, "qianjiapp.db")
    else:
        print(f"  [skip] Qianji DB not found: {_QIANJI_DB}")

    if not args.db_only:
        for prefix, remote, label in [
            ("Portfolio_Positions", "positions.csv", "positions CSV"),
            ("Accounts_History", "history.csv", "history CSV"),
        ]:
            found = _find_latest(_DOWNLOADS, prefix)
            if found:
                if prefix == "Portfolio_Positions":
                    positions_path = found
                _sync_file(found, remote)
            else:
                print(f"  [skip] No {label} in {_DOWNLOADS}")

        config_path = _PROJECT_DIR / "config.json"
        if config_path.exists():
            _sync_file(config_path, "config.json")

    # Upload sync metadata for freshness tracking
    if uploaded > 0:
        _upload_meta(positions_path, dry_run=args.dry_run)

    if not args.dry_run:
        _save_state(state)

    print(f"Done: {uploaded} file(s) uploaded")


if __name__ == "__main__":
    main()
