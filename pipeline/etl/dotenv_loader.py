"""Load pipeline/.env on module import (if present).

Existing environment variables take precedence — .env is a development
convenience for devs who haven't run `setx`, not a priority override.

Usage: import this module at the top of any entry script that reads env vars.
    import etl.dotenv_loader  # noqa: F401  (side effect: load .env)
"""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

_PIPELINE_DIR = Path(__file__).resolve().parent.parent
_ENV_PATH = _PIPELINE_DIR / ".env"


def _load() -> None:
    """Read ``_ENV_PATH`` into ``os.environ``. override=False so existing env wins.

    Indirection lets tests monkeypatch ``_ENV_PATH`` and call ``_load()``
    without triggering a full module reload (which would reset the path).
    """
    # override=False → existing env vars win over .env entries
    load_dotenv(dotenv_path=_ENV_PATH, override=False)


# Side effect on import: populate os.environ from pipeline/.env if it exists.
_load()
