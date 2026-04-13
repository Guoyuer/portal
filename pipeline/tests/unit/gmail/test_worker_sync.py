"""Tests for POST /mail/sync HTTP wrapper."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from gmail.worker_sync import SyncResult, WorkerSyncClient, WorkerSyncError


class TestWorkerSyncClient:
    @patch("gmail.worker_sync.httpx.Client")
    def test_returns_result_on_200(self, mock_cls: MagicMock) -> None:
        client = MagicMock()
        mock_cls.return_value.__enter__.return_value = client
        client.post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"inserted": 5, "skipped_existing": 3}),
        )
        c = WorkerSyncClient(base_url="https://w.example", secret="s")
        r = c.sync(
            classified_at="2026-04-12T22:00:00Z",
            emails=[{
                "msg_id": "<1>", "received_at": "2026-04-12T10:00:00Z",
                "classified_at": "2026-04-12T22:00:00Z",
                "sender": "a@b", "subject": "hi", "summary": "test",
                "category": "IMPORTANT",
            }],
        )
        assert r == SyncResult(inserted=5, skipped=3)

        call = client.post.call_args
        assert call.args[0] == "https://w.example/mail/sync"
        assert call.kwargs["headers"]["X-Sync-Secret"] == "s"
        body = call.kwargs["json"]
        assert len(body["emails"]) == 1

    @patch("gmail.worker_sync.httpx.Client")
    def test_raises_on_non_200(self, mock_cls: MagicMock) -> None:
        client = MagicMock()
        mock_cls.return_value.__enter__.return_value = client
        client.post.return_value = MagicMock(status_code=401, text="unauthorized")
        c = WorkerSyncClient(base_url="https://w.example", secret="wrong")
        with pytest.raises(WorkerSyncError):
            c.sync(classified_at="x", emails=[])

    @patch("gmail.worker_sync.httpx.Client")
    def test_raises_on_network_error(self, mock_cls: MagicMock) -> None:
        import httpx
        client = MagicMock()
        mock_cls.return_value.__enter__.return_value = client
        client.post.side_effect = httpx.ConnectError("dns failed")
        c = WorkerSyncClient(base_url="https://w.example", secret="s")
        with pytest.raises(WorkerSyncError):
            c.sync(classified_at="x", emails=[])
