"""Tests for sync metadata merge logic."""

from __future__ import annotations

from sync import _merge_meta

# ── Pure merge logic ───────────────────────────────────────────────────────


class TestMergeMetaPreservesValues:
    """_merge_meta keeps existing valid values when incoming is unknown."""

    def test_incoming_real_overwrites_existing(self) -> None:
        existing = {"positions_date": "Mar 01 10:00"}
        incoming = {"positions_date": "Mar 05 11:00"}
        assert _merge_meta(existing, incoming)["positions_date"] == "Mar 05 11:00"

    def test_incoming_question_mark_preserves_existing(self) -> None:
        existing = {"positions_date": "Mar 01 10:00"}
        incoming = {"positions_date": "?"}
        assert _merge_meta(existing, incoming)["positions_date"] == "Mar 01 10:00"

    def test_incoming_none_preserves_existing(self) -> None:
        existing = {"positions_file": "positions.csv"}
        incoming = {"positions_file": None}
        assert _merge_meta(existing, incoming)["positions_file"] == "positions.csv"

    def test_existing_unknown_overwritten_by_real(self) -> None:
        existing = {"qianji_date": "?"}
        incoming = {"qianji_date": "Apr 01 09:00"}
        assert _merge_meta(existing, incoming)["qianji_date"] == "Apr 01 09:00"

    def test_both_unknown_stays_unknown(self) -> None:
        existing = {"positions_date": "?"}
        incoming = {"positions_date": "?"}
        assert _merge_meta(existing, incoming)["positions_date"] == "?"

    def test_synced_at_always_uses_incoming(self) -> None:
        existing = {"synced_at": "2026-04-01T00:00:00+00:00"}
        incoming = {"synced_at": "2026-04-05T12:00:00+00:00"}
        assert _merge_meta(existing, incoming)["synced_at"] == "2026-04-05T12:00:00+00:00"


class TestMergeMetaEdgeCases:
    def test_empty_existing_uses_incoming(self) -> None:
        incoming = {"synced_at": "2026-04-05T12:00:00+00:00", "positions_date": "?", "qianji_date": "Apr 01 09:00"}
        assert _merge_meta({}, incoming) == incoming

    def test_extra_key_in_existing_preserved(self) -> None:
        result = _merge_meta({"old_field": "value", "positions_date": "Mar 01"}, {"positions_date": "?"})
        assert result["old_field"] == "value"
        assert result["positions_date"] == "Mar 01"

    def test_does_not_mutate_inputs(self) -> None:
        existing = {"positions_date": "Mar 01 10:00"}
        incoming = {"positions_date": "?"}
        existing_copy, incoming_copy = dict(existing), dict(incoming)
        _merge_meta(existing, incoming)
        assert existing == existing_copy
        assert incoming == incoming_copy

    def test_full_two_computer_scenario(self) -> None:
        """Computer A (Windows, Qianji only) then Computer B (Mac, Fidelity only)."""
        after_a = {
            "synced_at": "2026-04-05T08:00:00+00:00",
            "positions_file": None,
            "positions_date": "?",
            "history_date": "?",
            "qianji_date": "Apr 05 08:00",
        }
        from_b = {
            "synced_at": "2026-04-05T12:00:00+00:00",
            "positions_file": "Portfolio_Positions_Apr-05-2026.csv",
            "positions_date": "Apr 05 10:30",
            "history_date": "Apr 05 10:30",
            "qianji_date": "?",
        }
        result = _merge_meta(after_a, from_b)
        assert result["synced_at"] == "2026-04-05T12:00:00+00:00"
        assert result["positions_file"] == "Portfolio_Positions_Apr-05-2026.csv"
        assert result["positions_date"] == "Apr 05 10:30"
        assert result["history_date"] == "Apr 05 10:30"
        assert result["qianji_date"] == "Apr 05 08:00"  # preserved from A
