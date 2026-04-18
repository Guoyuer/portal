"""Weekly D1 backup.

Exports both production D1 databases (``portal-db`` + ``portal-gmail``) via
``npx wrangler d1 export --remote``, gzips the resulting ``.sql`` files, and
uploads them as assets on a dated GitHub Release in the private repo. The
release serves as a cheap, versioned, off-Cloudflare recovery point — before
this, there was no backup path if the D1 instance was deleted, corrupted, or
a schema migration went wrong.

Runs from GitHub Actions (``.github/workflows/d1-backup.yml``) on a weekly
cron, but can also be invoked locally for testing. Requires:

- ``npx wrangler`` on PATH (installed under ``worker/`` and ``worker-gmail/``
  node_modules so the subprocess call works with their ``cwd``).
- ``CLOUDFLARE_API_TOKEN`` + ``CLOUDFLARE_ACCOUNT_ID`` in the environment
  (wrangler reads them directly — same pattern as ``sync_prices_nightly.py``).
- ``gh`` CLI authenticated (GitHub Actions provides this automatically via
  ``GH_TOKEN``; locally, ``gh auth login`` once).

Use ``--dry-run`` to export + gzip without creating a GitHub release (asserts
the produced ``.sql.gz`` files are non-trivial in size). Output lives in a
tempdir that is retained on dry-run so you can inspect it.
"""
from __future__ import annotations

import argparse
import gzip
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
_WORKER_DIR = _PROJECT_DIR / "worker"
_WORKER_GMAIL_DIR = _PROJECT_DIR / "worker-gmail"

# (db_name, wrangler cwd) — wrangler resolves the db id from the config file
# in cwd, so each export has to run from the Worker that owns the binding.
_DATABASES: list[tuple[str, Path]] = [
    ("portal-db", _WORKER_DIR),
    ("portal-gmail", _WORKER_GMAIL_DIR),
]

# Smoke-test floor. ``portal-db`` is ~12 MB raw; ``portal-gmail`` is ~150 KB
# raw (schema + ~3 months of triaged emails). 100 KB is a generous lower
# bound that catches a truncated export (wrangler dumps only the schema on
# download failure, ~1-2 KB) without false-positiving on small databases.
_MIN_SIZE_BYTES = 100 * 1024


# ── helpers ─────────────────────────────────────────────────────────────────


def _resolve(bin_name: str) -> str:
    """Resolve a CLI binary to its full path (handles ``.cmd`` shims on Windows)."""
    resolved = shutil.which(bin_name)
    if resolved is None:
        print(f"Error: `{bin_name}` not found in PATH.", file=sys.stderr)
        sys.exit(1)
    return resolved


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Weekly D1 backup to GitHub Release")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Export + gzip without creating a GitHub release (files retained in tempdir)",
    )
    return p.parse_args()


def _export_one(db_name: str, cwd: Path, out_path: Path) -> None:
    """Shell out to ``wrangler d1 export --remote --output=<path>``."""
    npx = _resolve("npx")
    cmd = [
        npx, "wrangler", "d1", "export", db_name,
        "--remote", f"--output={out_path}",
    ]
    print(f"  exporting {db_name} -> {out_path.name}")
    subprocess.run(cmd, cwd=str(cwd), check=True)


def _gzip_in_place(sql_path: Path) -> Path:
    """Compress ``foo.sql`` → ``foo.sql.gz`` and unlink the original."""
    gz_path = sql_path.with_suffix(sql_path.suffix + ".gz")
    with sql_path.open("rb") as src, gzip.open(gz_path, "wb", compresslevel=9) as dst:
        shutil.copyfileobj(src, dst)
    sql_path.unlink()
    return gz_path


def _create_release(tag: str, assets: list[Path]) -> None:
    """Create a dated GitHub Release and attach the gzipped exports."""
    gh = _resolve("gh")
    notes = (
        "Automated weekly D1 backup from the `d1-backup.yml` workflow.\n\n"
        "Restore with: `gunzip -c <asset>.sql.gz | npx wrangler d1 execute "
        "<db-name> --remote --file=/dev/stdin`.\n"
    )
    cmd = [
        gh, "release", "create", tag,
        "--title", f"D1 backup {tag.removeprefix('d1-backup-')}",
        "--notes", notes,
        *[str(p) for p in assets],
    ]
    print(f"  creating release {tag} with {len(assets)} asset(s)")
    subprocess.run(cmd, check=True)


def main() -> None:
    args = _parse_args()
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    tag = f"d1-backup-{datetime.now(UTC).strftime('%Y-%m-%d')}"

    # On dry-run, don't auto-delete the tempdir so the user can inspect output.
    tmp_ctx: tempfile.TemporaryDirectory[str] | None = None
    if args.dry_run:
        work_dir = Path(tempfile.mkdtemp(prefix="d1-backup-"))
    else:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="d1-backup-")
        work_dir = Path(tmp_ctx.name)

    try:
        print(f"Step 1: exporting {len(_DATABASES)} database(s) to {work_dir}")
        assets: list[Path] = []
        for db_name, cwd in _DATABASES:
            sql_path = work_dir / f"{db_name}-{stamp}.sql"
            _export_one(db_name, cwd, sql_path)
            size = sql_path.stat().st_size
            print(f"    raw:  {size:>10,} bytes")
            if size < _MIN_SIZE_BYTES:
                raise RuntimeError(
                    f"{db_name} export is suspiciously small "
                    f"({size} bytes < {_MIN_SIZE_BYTES}) — aborting"
                )
            gz_path = _gzip_in_place(sql_path)
            gz_size = gz_path.stat().st_size
            print(f"    gzip: {gz_size:>10,} bytes ({gz_path.name})")
            assets.append(gz_path)

        if args.dry_run:
            print(f"\n[dry-run] {len(assets)} asset(s) in {work_dir}")
            for a in assets:
                print(f"[dry-run]   {a} ({a.stat().st_size:,} bytes)")
            print("[dry-run] skipping `gh release create`")
            return

        print("Step 2: uploading to GitHub Release")
        _create_release(tag, assets)
        print("Done.")
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        print(f"ERROR: subprocess failed with exit code {e.returncode}", file=sys.stderr)
        sys.exit(e.returncode)
