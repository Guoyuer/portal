"""POST /mail/sync client.

Hits the Worker with all classified emails in one batch. Raises
WorkerSyncError on any non-200 or network error — the daily cron fails loudly
so GH Actions sends a notification. No retries in v1 (GitHub's own retry
settings + the daily cadence cover transient failures).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class WorkerSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class SyncResult:
    inserted: int
    skipped: int


class WorkerSyncClient:
    def __init__(self, *, base_url: str, secret: str, timeout: float = 15.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._secret = secret
        self._timeout = timeout

    def sync(self, *, classified_at: str, emails: list[dict[str, Any]]) -> SyncResult:
        body = {"classified_at": classified_at, "emails": emails}
        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.post(
                    f"{self._base_url}/mail/sync",
                    headers={"X-Sync-Secret": self._secret, "Content-Type": "application/json"},
                    json=body,
                )
        except httpx.HTTPError as e:
            raise WorkerSyncError(f"network error: {e}") from e

        if r.status_code != 200:
            raise WorkerSyncError(f"sync returned {r.status_code}: {r.text[:200]}")

        data = r.json()
        try:
            return SyncResult(
                inserted=int(data["inserted"]),
                skipped=int(data["skipped_existing"]),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise WorkerSyncError(f"sync returned invalid body: {data!r}") from e
