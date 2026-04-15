"""One-shot idempotent schema migrations.

Each module in this package exposes a single ``migrate(db_path: Path) -> None``
function that is safe to call repeatedly — the functions guard every mutation
with a ``PRAGMA table_info`` / existence check so they no-op once applied.

Called directly from :mod:`pipeline.scripts.build_timemachine_db` at the
appropriate point in the build sequence. There is no ordered migration
runner: each migration is invoked once from its natural point of
application, and the idempotency guards make re-runs harmless.
"""
from __future__ import annotations
