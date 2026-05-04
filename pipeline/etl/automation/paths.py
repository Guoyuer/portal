"""Filesystem path resolution for the sync orchestrator.

Every helper here respects an env-var override (used in tests) and falls back
to a platform-appropriate default. No module-level state depends on these
values — call the function at runtime so tests can monkeypatch env vars.
"""
from __future__ import annotations

import os
from pathlib import Path

# ── Static locations ─────────────────────────────────────────────────────────

# ``scripts/run_automation.py`` lives at <pipeline>/scripts/; the pipeline root
# is its parent. These are stable regardless of how the script is invoked.
_SCRIPT_PATH = Path(__file__).resolve()
PIPELINE_DIR = _SCRIPT_PATH.parent.parent.parent  # etl/automation/ → etl/ → pipeline/
SCRIPTS_DIR = PIPELINE_DIR / "scripts"
DATA_DIR = PIPELINE_DIR / "data"
MARKER = DATA_DIR / ".last_run"


# ── Env-aware helpers ────────────────────────────────────────────────────────

def get_db_path() -> Path:
    """timemachine.db location. Overridable via ``PORTAL_DB_PATH`` (used in tests)."""
    override = os.environ.get("PORTAL_DB_PATH")
    if override:
        return Path(override)
    return DATA_DIR / "timemachine.db"


def get_downloads_dir() -> Path:
    """Downloads folder: env override → %USERPROFILE%\\Downloads → ~/Downloads."""
    override = os.environ.get("PORTAL_DOWNLOADS")
    if override:
        return Path(override)
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        return Path(userprofile) / "Downloads"
    return Path.home() / "Downloads"


def get_qianji_db_path() -> Path:
    """Location of Qianji's Windows app DB."""
    appdata = os.environ.get("APPDATA")
    root = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return root / "com.mutangtech.qianji.win" / "qianji_flutter" / "qianjiapp.db"


def get_log_dir() -> Path:
    """Per-day log directory. Prefers ``%LOCALAPPDATA%\\portal\\logs`` on Windows."""
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        return Path(localappdata) / "portal" / "logs"
    return Path.home() / ".local" / "share" / "portal" / "logs"
