"""Unit tests for Fidelity ActionKind classification + the backfill migration.

Phase 2 — Task 13. Covers:
  * ``classify_fidelity_action`` for all canonical Fidelity action strings.
  * Consistency between ``_classify_action`` (existing action_type) and the
    new ``classify_fidelity_action`` (normalized ActionKind).
  * Idempotent backfill via ``etl.migrations.add_fidelity_action_kind.migrate``.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from etl.migrations.add_fidelity_action_kind import migrate
from etl.sources import ActionKind
from etl.sources.fidelity import classify_fidelity_action
from etl.sources.fidelity.parse import _classify_action


class TestClassifyFidelityAction:
    """Confirm the enum mapping preserves Fidelity's existing semantics."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # Position-impacting
            ("YOU BOUGHT FXAIX", ActionKind.BUY),
            ('"YOU BOUGHT" FXAIX', ActionKind.BUY),
            ("YOU SOLD VTI", ActionKind.SELL),
            ("REINVESTMENT SPAXX", ActionKind.REINVESTMENT),
            # Cash-event actions
            ("DIVIDEND RECEIVED VTI", ActionKind.DIVIDEND),
            ("Electronic Funds Transfer", ActionKind.DEPOSIT),
            ("DIRECT DEPOSIT", ActionKind.DEPOSIT),
            ("DIRECT DEBIT", ActionKind.WITHDRAWAL),
            ("DEBIT CARD", ActionKind.WITHDRAWAL),
            ("CASH ADVANCE", ActionKind.WITHDRAWAL),
            # Internal transfers — all bucket to TRANSFER
            ("TRANSFERRED FROM BROKERAGE", ActionKind.TRANSFER),
            ("ROLLOVER CASH", ActionKind.TRANSFER),
            ("CONV TO ROTH", ActionKind.TRANSFER),
            ("ROTH CONVERSION", ActionKind.TRANSFER),
            ("PARTIAL CY RECHAR", ActionKind.TRANSFER),
            ("EARLY DIST FROM IRA", ActionKind.TRANSFER),
            # IRA cash contributions function as deposits
            ("CASH CONTRIBUTION", ActionKind.DEPOSIT),
            # Position-prefix but not cost-basis-impacting — qty-only kinds
            # that the replay primitive handles via ``qty += q``.
            ("REDEMPTION PAYOUT SGOV", ActionKind.REDEMPTION),
            ("DISTRIBUTION VTI", ActionKind.DISTRIBUTION),
            ("EXCHANGED TO FZFXX", ActionKind.EXCHANGE),
            # Pass-through / informational
            ("INTEREST EARNED", ActionKind.OTHER),
            ("FOREIGN TAX WITHHELD", ActionKind.OTHER),
            ("YOU LOANED", ActionKind.OTHER),
            ("LOAN RETURNED", ActionKind.OTHER),
            ("INCREASE COLLATERAL", ActionKind.OTHER),
            ("DECREASE COLLATERAL", ActionKind.OTHER),
            # Unknown / empty
            ("SOME BRAND NEW ACTION", ActionKind.OTHER),
            ("", ActionKind.OTHER),
        ],
    )
    def test_classification(self, raw: str, expected: ActionKind) -> None:
        assert classify_fidelity_action(raw) == expected

    def test_every_classified_action_type_has_a_kind(self) -> None:
        """If ``_classify_action`` returns an ACT_*, the mapping must produce
        a defined ActionKind — never accidentally fall through to OTHER for a
        known type.
        """
        # Representative raw strings exercising every branch of _ACTION_RULES
        samples = [
            "YOU BOUGHT X", "YOU SOLD X", "REINVESTMENT X",
            "DIVIDEND RECEIVED X", "REDEMPTION PAYOUT X", "DISTRIBUTION X",
            "EXCHANGED TO X", "Electronic Funds Transfer", "DIRECT DEPOSIT",
            "CASH CONTRIBUTION", "CONV TO ROTH", "ROTH CONVERSION",
            "EARLY DIST", "PARTIAL CY RECHAR", "ROLLOVER CASH", "TRANSFERRED X",
            "INTEREST", "YOU LOANED", "LOAN RETURNED",
            "INCREASE COLLATERAL", "DECREASE COLLATERAL",
            "CASH ADVANCE", "DIRECT DEBIT", "DEBIT CARD", "FOREIGN TAX",
        ]
        for raw in samples:
            act_type = _classify_action(raw)
            kind = classify_fidelity_action(raw)
            # Every row should produce a defined enum value (not crash).
            assert isinstance(kind, ActionKind), f"raw={raw!r} act_type={act_type!r}"


class TestBackfillMigration:
    """Exercise :func:`etl.migrations.add_fidelity_action_kind.migrate`."""

    def _make_legacy_db(self, tmp_path: Path) -> Path:
        """Create a DB missing the action_kind column (simulates a pre-refactor DB)."""
        db = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            """
            CREATE TABLE fidelity_transactions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date        TEXT NOT NULL,
                account         TEXT NOT NULL,
                account_number  TEXT NOT NULL,
                action          TEXT NOT NULL,
                action_type     TEXT NOT NULL DEFAULT '',
                symbol          TEXT NOT NULL DEFAULT '',
                description     TEXT NOT NULL DEFAULT '',
                lot_type        TEXT NOT NULL DEFAULT '',
                quantity        REAL NOT NULL DEFAULT 0,
                price           REAL NOT NULL DEFAULT 0,
                amount          REAL NOT NULL DEFAULT 0,
                settlement_date TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.executemany(
            "INSERT INTO fidelity_transactions "
            "(run_date, account, account_number, action, action_type, symbol, quantity, amount) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [
                ("2024-01-02", "Brokerage", "X1", "YOU BOUGHT VTI", "buy", "VTI", 10, -2000),
                ("2024-01-03", "Brokerage", "X1", "YOU SOLD VTI", "sell", "VTI", -5, 1100),
                ("2024-01-04", "Brokerage", "X1", "DIVIDEND RECEIVED VTI", "dividend", "VTI", 0, 12),
            ],
        )
        conn.commit()
        conn.close()
        return db

    def test_backfill_adds_column_and_populates(self, tmp_path: Path) -> None:
        db = self._make_legacy_db(tmp_path)

        touched = migrate(db)
        assert touched == 3

        conn = sqlite3.connect(str(db))
        kinds = [r[0] for r in conn.execute(
            "SELECT action_kind FROM fidelity_transactions ORDER BY id"
        )]
        conn.close()
        assert kinds == [
            ActionKind.BUY.value,
            ActionKind.SELL.value,
            ActionKind.DIVIDEND.value,
        ]

    def test_backfill_is_idempotent(self, tmp_path: Path) -> None:
        db = self._make_legacy_db(tmp_path)
        first = migrate(db)
        second = migrate(db)
        assert first == 3
        assert second == 0  # all rows already have a kind

    def test_backfill_is_noop_on_fresh_schema(self, empty_db: Path) -> None:
        """A fresh DB already has the column — migrate() must not error or
        touch populated rows."""
        conn = sqlite3.connect(str(empty_db))
        conn.execute(
            "INSERT INTO fidelity_transactions "
            "(run_date, account_number, action, action_type, action_kind, symbol) "
            "VALUES (?,?,?,?,?,?)",
            ("2024-02-01", "X1", "YOU BOUGHT FOO", "buy", ActionKind.BUY.value, "FOO"),
        )
        conn.commit()
        conn.close()

        touched = migrate(empty_db)
        assert touched == 0  # nothing needing backfill

        conn = sqlite3.connect(str(empty_db))
        row = conn.execute("SELECT action_kind FROM fidelity_transactions").fetchone()
        conn.close()
        assert row[0] == ActionKind.BUY.value

    def test_backfill_resyncs_stale_kinds(self, empty_db: Path) -> None:
        """When a row's stored ``action_kind`` disagrees with the current
        classifier (e.g. after the REDEMPTION / DISTRIBUTION / EXCHANGE
        widening moved those from OTHER to their own kinds), migrate() must
        overwrite the stale value instead of leaving it behind.
        """
        conn = sqlite3.connect(str(empty_db))
        conn.executemany(
            "INSERT INTO fidelity_transactions "
            "(run_date, account_number, action, action_type, action_kind, symbol) "
            "VALUES (?,?,?,?,?,?)",
            [
                # Legacy "other" for what's now REDEMPTION
                ("2024-02-01", "X1", "REDEMPTION PAYOUT SGOV", "redemption",
                 ActionKind.OTHER.value, "91279Q"),
                # Legacy "other" for what's now DISTRIBUTION
                ("2024-02-02", "X1", "DISTRIBUTION VTI", "distribution",
                 ActionKind.OTHER.value, "VTI"),
                # Already correct — must NOT be re-written
                ("2024-02-03", "X1", "YOU BOUGHT VTI", "buy",
                 ActionKind.BUY.value, "VTI"),
            ],
        )
        conn.commit()
        conn.close()

        touched = migrate(empty_db)
        assert touched == 2  # only the two stale rows

        conn = sqlite3.connect(str(empty_db))
        kinds = [r[0] for r in conn.execute(
            "SELECT action_kind FROM fidelity_transactions ORDER BY id"
        )]
        conn.close()
        assert kinds == [
            ActionKind.REDEMPTION.value,
            ActionKind.DISTRIBUTION.value,
            ActionKind.BUY.value,
        ]
