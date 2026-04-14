"""Parity check: the committed ``src/lib/schemas/_generated.ts`` must match
what ``tools/gen_zod.py`` emits from the current ``etl/types.py``.

Catches the class of drift where someone adds a field to ``AllocationRow``
or ``TickerDetail`` on the Python side and forgets to regenerate.
"""
from __future__ import annotations

# Make `tools` importable by inserting the pipeline root.
import sys
from pathlib import Path

import pytest

_PIPELINE_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PIPELINE_ROOT))

from tools.gen_zod import render_zod  # noqa: E402

_TYPES_PY = _PIPELINE_ROOT / "etl" / "types.py"
_GENERATED_TS = _PIPELINE_ROOT.parent / "src" / "lib" / "schemas" / "_generated.ts"


class TestGeneratedZodInSync:
    def test_committed_file_matches_generator(self):
        if not _GENERATED_TS.exists():
            pytest.skip(f"{_GENERATED_TS} not found (run gen_zod.py --write)")
        expected = render_zod(_TYPES_PY).strip()
        actual = _GENERATED_TS.read_text(encoding="utf-8").strip()
        if expected != actual:
            msg = (
                f"Generated Zod schema out of date.\n"
                f"Run: cd pipeline && .venv/Scripts/python.exe tools/gen_zod.py "
                f"--write {_GENERATED_TS}"
            )
            pytest.fail(msg)

    def test_camel_case_conversion_applied(self):
        """Smoke: the key field renames actually happen."""
        rendered = render_zod(_TYPES_PY)
        # snake names must not appear on the TS side
        assert "us_equity" not in rendered
        assert "cost_basis" not in rendered
        # camel names must
        assert "usEquity" in rendered
        assert "costBasis" in rendered
