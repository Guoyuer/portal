"""Export, verify, and publish R2 JSON artifacts.

The exporter reads the local SQLite API projections, writes endpoint-shaped
JSON files, then verifies hashes, row counts, latest-date coverage, and
frontend Zod schema compatibility. Publication is manifest-last: snapshot
objects are uploaded/read-back verified before ``manifest.json`` is switched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

# ruff: noqa: E402

_PROJECT_DIR = Path(__file__).resolve().parent.parent
_REPO_DIR = _PROJECT_DIR.parent
_WORKER_DIR = _REPO_DIR / "worker"
_DEFAULT_DB_PATH = Path(os.environ.get("PORTAL_DB_PATH", str(_PROJECT_DIR / "data" / "timemachine.db")))
_DEFAULT_ARTIFACT_DIR = _PROJECT_DIR / "artifacts" / "r2"
_LOCK_PATH = _PROJECT_DIR / "data" / ".r2-publisher.lock"
_BUCKET_NAME = "portal-data"
_CONTENT_TYPE_JSON = "application/json"
_ENDPOINTS = ("timeline", "econ", "prices")

sys.path.insert(0, str(_PROJECT_DIR))
import etl.dotenv_loader  # noqa: F401  (side effect: load pipeline/.env)

JsonDict = dict[str, Any]


# ── Single-publisher lock ─────────────────────────────────────────────────


@contextmanager
def _single_publisher_lock(lock_path: Path = _LOCK_PATH) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        detail = lock_path.read_text(encoding="utf-8", errors="replace") if lock_path.exists() else ""
        msg = f"another R2 publisher appears to be running: {lock_path}"
        if detail:
            msg += f"\nlock detail: {detail.strip()}"
        raise RuntimeError(msg) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"pid={os.getpid()} started={_generated_at_from(_utc_now())}\n")
        yield
    finally:
        lock_path.unlink(missing_ok=True)


# ── JSON/filesystem helpers ────────────────────────────────────────────────


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _version_from(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H%M%SZ")


def _generated_at_from(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_commit() -> str:
    try:
        return subprocess.check_output(  # noqa: S603
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_REPO_DIR),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (FileNotFoundError, OSError, subprocess.CalledProcessError):
        return "unknown"


def _json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _write_json(path: Path, payload: object) -> JsonDict:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _json_bytes(payload)
    path.write_bytes(data)
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "contentType": _CONTENT_TYPE_JSON,
    }


def _read_json(path: Path) -> JsonDict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"expected JSON object: {path}"
        raise RuntimeError(msg)
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _descriptor(key: str, path: Path, payload: object) -> JsonDict:
    d = _write_json(path, payload)
    return {"key": key, **d}


def _artifact_path(artifact_dir: Path, key: str) -> Path:
    return artifact_dir / Path(*key.split("/"))


def _snapshot_descriptor(
    *,
    version: str,
    snapshot_dir: Path,
    endpoint: str,
    payload: object,
) -> JsonDict:
    return _descriptor(
        f"snapshots/{version}/{endpoint}.json",
        snapshot_dir / f"{endpoint}.json",
        payload,
    )


# ── SQLite shape loaders ───────────────────────────────────────────────────


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        msg = f"SQLite DB not found: {db_path}"
        raise RuntimeError(msg)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn: sqlite3.Connection, sql: str, params: Iterable[object] = ()) -> list[JsonDict]:
    return [dict(row) for row in conn.execute(sql, tuple(params))]


def _scalar(conn: sqlite3.Connection, sql: str) -> Any:
    row = conn.execute(sql).fetchone()
    return row[0] if row else None


def _row_count(conn: sqlite3.Connection, table_or_view: str) -> int:
    return int(_scalar(conn, f"SELECT COUNT(*) FROM {table_or_view}") or 0)  # noqa: S608


def _json_array(raw: object, *, label: str) -> list[Any]:
    parsed = json.loads(str(raw))
    if not isinstance(parsed, list):
        msg = f"{label} is not a JSON array"
        raise RuntimeError(msg)
    return parsed


_DAILY_SQL = """
SELECT date, total, us_equity AS usEquity, non_us_equity AS nonUsEquity,
  crypto, safe_net AS safeNet, liabilities
FROM computed_daily
ORDER BY date
"""

_DAILY_TICKERS_SQL = """
SELECT date, ticker, value, category, subtype,
  cost_basis AS costBasis, gain_loss AS gainLoss, gain_loss_pct AS gainLossPct
FROM computed_daily_tickers
ORDER BY date, value DESC
"""

_FIDELITY_TXNS_SQL = """
SELECT run_date AS runDate, action_type AS actionType, symbol, amount,
  quantity, price
FROM fidelity_transactions
ORDER BY runDate, symbol, actionType, amount, quantity, price
"""

_QIANJI_TXNS_SQL = """
SELECT date, type, category, amount,
  is_retirement AS isRetirement,
  account_to AS accountTo
FROM qianji_transactions
ORDER BY date
"""

_ROBINHOOD_TXNS_SQL = """
SELECT txn_date AS txnDate, action, action_kind AS actionKind,
  ticker, quantity, amount_usd AS amountUsd,
  raw_description AS rawDescription
FROM robinhood_transactions
ORDER BY txnDate
"""

_EMPOWER_CONTRIBUTIONS_SQL = """
SELECT date, amount, ticker, cusip
FROM empower_contributions
ORDER BY date
"""

_CATEGORIES_SQL = """
SELECT key, name,
  display_order AS displayOrder,
  target_pct AS targetPct
FROM categories
ORDER BY display_order
"""

_MARKET_INDICES_SQL = """
SELECT ticker, name, current, month_return AS monthReturn,
  ytd_return AS ytdReturn, high_52w AS high52w, low_52w AS low52w, sparkline
FROM computed_market_indices
ORDER BY ticker
"""

_HOLDINGS_DETAIL_SQL = """
SELECT ticker, month_return AS monthReturn, start_value AS startValue,
  end_value AS endValue, high_52w AS high52w, low_52w AS low52w, vs_high AS vsHigh
FROM computed_holdings_detail
ORDER BY month_return DESC
"""

_ECON_SNAPSHOT_SQL = """
SELECT key, value
FROM econ_series t1
WHERE date = (SELECT MAX(date) FROM econ_series t2 WHERE t2.key = t1.key)
"""

_ECON_SERIES_GROUPED_SQL = """
SELECT key,
  json_group_array(json_object('date', date, 'value', value)) AS points
FROM (SELECT key, date, value FROM econ_series ORDER BY key, date)
GROUP BY key
ORDER BY key
"""


def _price_symbols(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT symbol FROM daily_close WHERE symbol <> ''
        UNION
        SELECT symbol FROM fidelity_transactions WHERE symbol <> ''
        ORDER BY symbol
        """
    )
    return [str(row[0]).upper() for row in rows]


def _market_indices(conn: sqlite3.Connection) -> list[JsonDict]:
    rows = _rows(conn, _MARKET_INDICES_SQL)
    for row in rows:
        raw = row.get("sparkline")
        if raw in (None, ""):
            row["sparkline"] = None
            continue
        row["sparkline"] = _json_array(raw, label=f"market sparkline for {row.get('ticker')}")
    return rows


def _qianji_txns(conn: sqlite3.Connection) -> list[JsonDict]:
    rows = _rows(conn, _QIANJI_TXNS_SQL)
    for row in rows:
        row["isRetirement"] = bool(row["isRetirement"])
    return rows


def _build_timeline(conn: sqlite3.Connection, *, version: str, generated_at: str) -> JsonDict:
    daily = _rows(conn, _DAILY_SQL)
    categories = _rows(conn, _CATEGORIES_SQL)
    if not daily:
        msg = "computed_daily is empty; refusing to export timeline.json"
        raise RuntimeError(msg)
    if not categories:
        msg = "categories is empty; refusing to export timeline.json"
        raise RuntimeError(msg)

    latest_date = str(daily[-1]["date"])
    return {
        "daily": daily,
        "dailyTickers": _rows(conn, _DAILY_TICKERS_SQL),
        "fidelityTxns": _rows(conn, _FIDELITY_TXNS_SQL),
        "qianjiTxns": _qianji_txns(conn),
        "robinhoodTxns": _rows(conn, _ROBINHOOD_TXNS_SQL),
        "empowerContributions": _rows(conn, _EMPOWER_CONTRIBUTIONS_SQL),
        "categories": categories,
        "market": {"indices": _market_indices(conn)},
        "holdingsDetail": _rows(conn, _HOLDINGS_DETAIL_SQL),
        "syncMeta": {
            "backend": "r2",
            "version": version,
            "last_sync": generated_at,
            "last_date": latest_date,
        },
    }


def _build_econ(conn: sqlite3.Connection, *, generated_at: str) -> JsonDict:
    snapshot = {
        str(row["key"]): row["value"]
        for row in _rows(conn, _ECON_SNAPSHOT_SQL)
    }
    series = {
        str(row["key"]): _json_array(row["points"], label=f"econ series {row['key']}")
        for row in _rows(conn, _ECON_SERIES_GROUPED_SQL)
    }
    return {"generatedAt": generated_at, "snapshot": snapshot, "series": series}


def _build_price(conn: sqlite3.Connection, symbol: str) -> JsonDict:
    return {
        "symbol": symbol,
        "prices": _rows(
            conn,
            "SELECT date, close FROM daily_close WHERE symbol = ? ORDER BY date",
            (symbol,),
        ),
        "transactions": _rows(
            conn,
            """
            SELECT run_date AS runDate, action_type AS actionType, quantity, price, amount
            FROM fidelity_transactions
            WHERE symbol = ?
            ORDER BY run_date, action_type, amount, quantity, price
            """,
            (symbol,),
        ),
    }


def _build_prices_bundle(conn: sqlite3.Connection) -> tuple[JsonDict, JsonDict]:
    prices: JsonDict = {}
    row_counts: JsonDict = {}
    for symbol in _price_symbols(conn):
        payload = _build_price(conn, symbol)
        prices[symbol] = payload
        row_counts[symbol] = {
            "priceRows": len(payload["prices"]),
            "transactionRows": len(payload["transactions"]),
        }
    return prices, row_counts


def _sqlite_row_counts(conn: sqlite3.Connection) -> JsonDict:
    return {
        "daily": _row_count(conn, "computed_daily"),
        "dailyTickers": _row_count(conn, "computed_daily_tickers"),
        "fidelityTxns": _row_count(conn, "fidelity_transactions"),
        "qianjiTxns": _row_count(conn, "qianji_transactions"),
        "robinhoodTxns": _row_count(conn, "robinhood_transactions"),
        "empowerContributions": _row_count(conn, "empower_contributions"),
        "categories": _row_count(conn, "categories"),
        "marketIndices": _row_count(conn, "computed_market_indices"),
        "holdingsDetail": _row_count(conn, "computed_holdings_detail"),
        "econSeries": int(_scalar(conn, "SELECT COUNT(DISTINCT key) FROM econ_series") or 0),
        "econSnapshot": int(_scalar(conn, "SELECT COUNT(DISTINCT key) FROM econ_series") or 0),
    }


def _json_row_counts(timeline: Mapping[str, Any], econ: Mapping[str, Any]) -> JsonDict:
    market = timeline.get("market") or {}
    return {
        "daily": len(timeline["daily"]),
        "dailyTickers": len(timeline["dailyTickers"]),
        "fidelityTxns": len(timeline["fidelityTxns"]),
        "qianjiTxns": len(timeline["qianjiTxns"]),
        "robinhoodTxns": len(timeline["robinhoodTxns"]),
        "empowerContributions": len(timeline["empowerContributions"]),
        "categories": len(timeline["categories"]),
        "marketIndices": len(market.get("indices") or []),
        "holdingsDetail": len(timeline["holdingsDetail"] or []),
        "econSeries": len(econ["series"]),
        "econSnapshot": len(econ["snapshot"]),
    }


# ── Export ─────────────────────────────────────────────────────────────────


def export_artifacts(
    *,
    db_path: Path = _DEFAULT_DB_PATH,
    artifact_dir: Path = _DEFAULT_ARTIFACT_DIR,
    version: str | None = None,
    generated_at: str | None = None,
) -> JsonDict:
    now = _utc_now()
    version = version or _version_from(now)
    generated_at = generated_at or _generated_at_from(now)
    snapshot_dir = artifact_dir / "snapshots" / version
    if snapshot_dir.exists():
        msg = f"snapshot version already exists locally: {snapshot_dir}"
        raise RuntimeError(msg)

    conn = _connect_ro(db_path)
    try:
        latest_date = str(_scalar(conn, "SELECT MAX(date) FROM computed_daily") or "")
        if not latest_date:
            msg = "computed_daily has no MAX(date); refusing to export"
            raise RuntimeError(msg)

        timeline = _build_timeline(conn, version=version, generated_at=generated_at)
        econ = _build_econ(conn, generated_at=generated_at)

        prices, price_row_counts = _build_prices_bundle(conn)
        endpoint_payloads = {"timeline": timeline, "econ": econ, "prices": prices}
        objects = {
            endpoint: _snapshot_descriptor(
                version=version,
                snapshot_dir=snapshot_dir,
                endpoint=endpoint,
                payload=endpoint_payloads[endpoint],
            )
            for endpoint in _ENDPOINTS
        }

        row_counts = _sqlite_row_counts(conn)
        json_counts = _json_row_counts(timeline, econ)
        if row_counts != json_counts:
            msg = f"SQLite row counts do not match exported JSON counts: sqlite={row_counts}, json={json_counts}"
            raise RuntimeError(msg)

        manifest = {
            "version": version,
            "generatedAt": generated_at,
            "source": {"gitCommit": _git_commit(), "latestDate": latest_date},
            "objects": objects,
        }
        manifest_path = artifact_dir / "manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_bytes(_json_bytes(manifest))

        summary = {
            "version": version,
            "generatedAt": generated_at,
            "source": manifest["source"],
            "rowCounts": row_counts,
            "priceRowCounts": price_row_counts,
            "objectCount": len(objects),
            "totalBytes": sum(int(d["bytes"]) for d in objects.values()),
        }
        _write_json(artifact_dir / "reports" / "export-summary.json", summary)
        return manifest
    finally:
        conn.close()


# ── Verify ─────────────────────────────────────────────────────────────────


def _expect_equal(label: str, left: object, right: object) -> None:
    if left == right:
        return
    msg = f"{label} mismatch: {left!r} != {right!r}"
    raise RuntimeError(msg)


def _verify_descriptor(artifact_dir: Path, label: str, descriptor: Mapping[str, Any]) -> None:
    key = str(descriptor["key"])
    path = _artifact_path(artifact_dir, key)
    if not path.exists():
        msg = f"{label} missing artifact: {key}"
        raise RuntimeError(msg)
    data = path.read_bytes()
    if not data:
        msg = f"{label} artifact is empty: {key}"
        raise RuntimeError(msg)
    _expect_equal(f"{label} bytes", len(data), int(descriptor["bytes"]))
    _expect_equal(f"{label} sha256", hashlib.sha256(data).hexdigest(), descriptor["sha256"])
    _expect_equal(f"{label} contentType", descriptor.get("contentType"), _CONTENT_TYPE_JSON)


def _verify_row_counts(artifact_dir: Path, db_path: Path, manifest: Mapping[str, Any]) -> None:
    summary = _read_json(artifact_dir / "reports" / "export-summary.json")
    conn = _connect_ro(db_path)
    try:
        latest_date = str(_scalar(conn, "SELECT MAX(date) FROM computed_daily") or "")
        _expect_equal("latestDate", manifest["source"]["latestDate"], latest_date)
        _expect_equal("summary latestDate", summary["source"]["latestDate"], latest_date)

        timeline = _read_json(_artifact_path(artifact_dir, manifest["objects"]["timeline"]["key"]))
        econ = _read_json(_artifact_path(artifact_dir, manifest["objects"]["econ"]["key"]))
        sqlite_counts = _sqlite_row_counts(conn)
        json_counts = _json_row_counts(timeline, econ)
        _expect_equal("summary rowCounts", summary["rowCounts"], sqlite_counts)
        _expect_equal("JSON rowCounts", json_counts, sqlite_counts)

        prices_bundle = _read_json(_artifact_path(artifact_dir, manifest["objects"]["prices"]["key"]))
        expected_prices: JsonDict = {}
        expected_symbols = _price_symbols(conn)
        _expect_equal("prices bundle symbols", sorted(prices_bundle), expected_symbols)
        for symbol in expected_symbols:
            payload = prices_bundle[symbol]
            if not isinstance(payload, dict):
                msg = f"prices bundle payload for {symbol} is not a JSON object"
                raise RuntimeError(msg)
            _expect_equal(f"prices bundle symbol {symbol}", payload["symbol"], symbol)
            expected_prices[symbol] = {
                "priceRows": len(payload["prices"]),
                "transactionRows": len(payload["transactions"]),
            }
        _expect_equal("summary priceRowCounts", summary["priceRowCounts"], expected_prices)
    finally:
        conn.close()


def _run_schema_check(artifact_dir: Path) -> None:
    npx = shutil.which("npx")
    if npx is None:
        msg = "npx not found; cannot run frontend Zod artifact validation"
        raise RuntimeError(msg)
    cmd = [
        npx,
        "tsx",
        "scripts/validate_api_zod.ts",
        "artifacts",
        str(artifact_dir),
    ]
    result = subprocess.run(cmd, cwd=str(_REPO_DIR), capture_output=True, text=True)  # noqa: S603
    if result.returncode != 0:
        msg = (
            "frontend Zod artifact validation failed\n"
            f"stdout:\n{result.stdout or '(empty)'}\n"
            f"stderr:\n{result.stderr or '(empty)'}"
        )
        raise RuntimeError(msg)
    if result.stdout:
        print(result.stdout, end="")


def verify_artifacts(
    *,
    db_path: Path = _DEFAULT_DB_PATH,
    artifact_dir: Path = _DEFAULT_ARTIFACT_DIR,
    schema: bool = True,
) -> JsonDict:
    manifest_path = artifact_dir / "manifest.json"
    if not manifest_path.exists():
        msg = f"manifest not found: {manifest_path}"
        raise RuntimeError(msg)
    manifest = _read_json(manifest_path)

    for endpoint in _ENDPOINTS:
        _verify_descriptor(artifact_dir, endpoint, manifest["objects"][endpoint])

    _verify_row_counts(artifact_dir, db_path, manifest)
    if schema:
        _run_schema_check(artifact_dir)

    print(
        "R2 artifacts verified: "
        f"version={manifest['version']} objects={len(manifest['objects'])}"
    )
    return manifest


# ── Publish ────────────────────────────────────────────────────────────────


def _resolve_npx() -> str:
    npx = shutil.which("npx")
    if npx is None:
        msg = "npx not found in PATH"
        raise RuntimeError(msg)
    return npx


def _run_wrangler_r2(args: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = [_resolve_npx(), "wrangler", "r2", "object", *args]
    return subprocess.run(  # noqa: S603
        cmd,
        cwd=str(_WORKER_DIR),
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _remote_key(key: str) -> str:
    return f"{_BUCKET_NAME}/{key}"


def _wrangler_detail(result: CompletedProcess[str]) -> str:
    return f"stderr:\n{result.stderr or '(empty)'}\nstdout:\n{result.stdout or '(empty)'}"


def _object_absent(key: str, *, remote: bool) -> bool:
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        result = _run_wrangler_r2(
            ["get", _remote_key(key), "--remote" if remote else "--local", f"--file={tmp_path}"]
        )
        if result.returncode == 0:
            return False
        detail = (result.stderr + result.stdout).lower()
        if "specified key does not exist" in detail or "not found" in detail or "does not exist" in detail:
            return True
        msg = f"could not check R2 key existence for {key}\n{_wrangler_detail(result)}"
        raise RuntimeError(msg)
    finally:
        tmp_path.unlink(missing_ok=True)


def _assert_snapshot_key_absent(key: str, *, remote: bool) -> None:
    if _object_absent(key, remote=remote):
        return
    msg = f"R2 snapshot object already exists; refusing to overwrite: {key}"
    raise RuntimeError(msg)


def _put_wrangler_object(key: str, file_path: Path, *, remote: bool) -> None:
    result = _run_wrangler_r2(
        [
            "put",
            _remote_key(key),
            "--remote" if remote else "--local",
            f"--file={file_path}",
            f"--content-type={_CONTENT_TYPE_JSON}",
        ]
    )
    if result.returncode != 0:
        msg = f"wrangler r2 object put failed for {key}\n{_wrangler_detail(result)}"
        raise RuntimeError(msg)


def _readback_wrangler_object(key: str, descriptor: Mapping[str, Any], *, remote: bool) -> None:
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        result = _run_wrangler_r2(
            ["get", _remote_key(key), "--remote" if remote else "--local", f"--file={tmp_path}"]
        )
        if result.returncode != 0:
            msg = f"wrangler r2 object get failed for {key}\n{_wrangler_detail(result)}"
            raise RuntimeError(msg)
        _expect_equal(f"R2 readback bytes {key}", tmp_path.stat().st_size, int(descriptor["bytes"]))
        _expect_equal(f"R2 readback sha256 {key}", _sha256(tmp_path), descriptor["sha256"])
    finally:
        tmp_path.unlink(missing_ok=True)


def _publish_wrangler(artifact_dir: Path, descriptors: list[Mapping[str, Any]], *, remote: bool) -> None:
    mode = "remote" if remote else "local"
    total = len(descriptors)
    print(f"Checking {mode} R2 snapshot keys are absent: {total} objects")
    for idx, descriptor in enumerate(descriptors, start=1):
        if idx == 1 or idx == total or idx % 10 == 0:
            print(f"Checking R2 key {idx}/{total}: {descriptor['key']}")
        _assert_snapshot_key_absent(str(descriptor["key"]), remote=remote)

    print(f"Uploading and verifying {mode} R2 snapshot objects: {total} objects")
    for idx, descriptor in enumerate(descriptors, start=1):
        key = str(descriptor["key"])
        if idx == 1 or idx == total or idx % 10 == 0:
            print(f"Publishing R2 object {idx}/{total}: {key}")
        _put_wrangler_object(key, _artifact_path(artifact_dir, key), remote=remote)
        _readback_wrangler_object(key, descriptor, remote=remote)

    manifest_path = artifact_dir / "manifest.json"
    manifest_descriptor = {
        "key": "manifest.json",
        "sha256": _sha256(manifest_path),
        "bytes": manifest_path.stat().st_size,
        "contentType": _CONTENT_TYPE_JSON,
    }
    print("Publishing R2 manifest.json last")
    _put_wrangler_object("manifest.json", manifest_path, remote=remote)
    _readback_wrangler_object("manifest.json", manifest_descriptor, remote=remote)


def publish_artifacts(
    *,
    db_path: Path = _DEFAULT_DB_PATH,
    artifact_dir: Path = _DEFAULT_ARTIFACT_DIR,
    remote: bool = False,
    schema: bool = True,
) -> JsonDict:
    manifest = verify_artifacts(db_path=db_path, artifact_dir=artifact_dir, schema=schema)
    descriptors: list[Mapping[str, Any]] = list(manifest["objects"].values())
    with _single_publisher_lock():
        _publish_wrangler(artifact_dir, descriptors, remote=remote)
        mode = "remote" if remote else "local"
    print(f"Published R2 artifacts to {mode} {_BUCKET_NAME}: version={manifest['version']}")
    return manifest


# ── CLI ────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export, verify, and publish Portal R2 JSON artifacts")
    parser.add_argument("--db", type=Path, default=_DEFAULT_DB_PATH, help="Path to timemachine.db")
    parser.add_argument("--artifact-dir", type=Path, default=_DEFAULT_ARTIFACT_DIR, help="Local R2 artifact directory")

    sub = parser.add_subparsers(dest="command", required=True)

    export_p = sub.add_parser("export", help="Export endpoint-shaped artifacts from SQLite")
    export_p.add_argument("--version", default=None, help="Snapshot version id; default is current UTC timestamp")
    export_p.add_argument("--generated-at", default=None, help="ISO timestamp for payload metadata")

    verify_p = sub.add_parser("verify", help="Verify local artifact hashes, row counts, and Zod schemas")
    verify_p.add_argument("--skip-schema", action="store_true", help="Skip frontend Zod validation")

    publish_p = sub.add_parser("publish", help="Publish artifacts with manifest-last ordering")
    mode = publish_p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--local", action="store_true", help="Publish to Wrangler local R2")
    mode.add_argument("--remote", action="store_true", help="Publish to production R2")
    publish_p.add_argument("--skip-schema", action="store_true", help="Skip frontend Zod validation")

    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.command == "export":
            manifest = export_artifacts(
                db_path=args.db,
                artifact_dir=args.artifact_dir,
                version=args.version,
                generated_at=args.generated_at,
            )
            print(f"Exported R2 artifacts: version={manifest['version']} dir={args.artifact_dir}")
        elif args.command == "verify":
            verify_artifacts(
                db_path=args.db,
                artifact_dir=args.artifact_dir,
                schema=not args.skip_schema,
            )
        elif args.command == "publish":
            publish_artifacts(
                db_path=args.db,
                artifact_dir=args.artifact_dir,
                remote=args.remote,
                schema=not args.skip_schema,
            )
        else:  # pragma: no cover - argparse enforces choices
            msg = f"unknown command: {args.command}"
            raise RuntimeError(msg)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
