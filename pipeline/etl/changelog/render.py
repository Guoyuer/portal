"""Email body templating for the sync changelog.

:func:`format_text` / :func:`format_html` render the Jinja templates
under ``etl/changelog/templates/``; :func:`build_subject` produces the
short subject line. All category-specific rules (LARGE MOVE thresholds,
component-sum drift check, exit-gate labels, money/qty formatting) live
in :mod:`categorize`; this module preps data and invokes the templates.
"""
from __future__ import annotations

from typing import Any

from jinja2 import Environment, PackageLoader, StrictUndefined

from .categorize import (
    _LARGE_MOVE_PCT,
    _LARGE_MOVE_USD,
    _component_check_line,
    _fmt_delta,
    _fmt_money,
    _fmt_qty,
    _gate_for_exit,
    _render_component_row,
)
from .snapshot import SyncChangelog

_ENV = Environment(
    loader=PackageLoader("etl.changelog", "templates"),
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=False,
    extensions=["jinja2.ext.do"],
    undefined=StrictUndefined,
)

# Expose rendering + gate helpers and the LARGE MOVE thresholds so the
# template can do its own structural branching without an intermediate
# view-model dict. Money/qty helpers pass-through unchanged.
_ENV.globals.update(
    fmt_money=_fmt_money,
    fmt_delta=_fmt_delta,
    fmt_qty=_fmt_qty,
    gate_for_exit=_gate_for_exit,
    render_component_row=_render_component_row,
    component_check_line=_component_check_line,
    LARGE_MOVE_USD=_LARGE_MOVE_USD,
    LARGE_MOVE_PCT=_LARGE_MOVE_PCT,
)


def format_text(changelog: SyncChangelog, context: dict[str, Any]) -> str:
    """Plain-text email body, rendered from ``templates/email.txt.jinja``."""
    return _ENV.get_template("email.txt.jinja").render(
        changelog=changelog,
        timestamp=context.get("timestamp", ""),
        status_label=context.get("status_label", "OK"),
        exit_code=int(context.get("exit_code", 0) or 0),
        error=context.get("error"),
        warnings=context.get("warnings") or [],
        log_file=context.get("log_file", ""),
        duration=context.get("duration", ""),
        qianji_total=sum(tot for _c, tot in changelog.qianji_added_by_category.values()),
    )


def format_html(changelog: SyncChangelog, context: dict[str, Any]) -> str:
    """HTML email body — wraps the plain-text render in a ``<pre>`` block.

    Hand-escapes the <pre> body (& < > only) to match the original's
    minimal escape set; Jinja's ``|e`` filter would additionally escape
    ``"`` and ``'`` which Gmail renders as literal ``&#34;`` / ``&#39;``.
    """
    exit_code = int(context.get("exit_code", 0) or 0)
    text = format_text(changelog, context)
    return _ENV.get_template("email.html.jinja").render(
        text_body=text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"),
        status_label=context.get("status_label", "OK"),
        header_color="#2e7d32" if exit_code == 0 else "#c62828",
    )


def build_subject(
    changelog: SyncChangelog,
    exit_code: int,
    status_label: str | None = None,
) -> str:
    """Short, informative subject line.

    Successful syncs with changes → summary of counts. Failures → ``FAIL —
    <label>`` so the operator can triage from the inbox row alone, with an
    ``(exit N)`` fallback when no label was provided. The label comes from
    :data:`etl.automation._constants._STATUS_LABELS`; the caller resolves
    it (rather than importing here) so this module stays free of any
    `etl.automation` dependency.
    """
    if exit_code != 0:
        if status_label is None:
            return f"[Portal Sync] FAIL (exit {exit_code})"
        return f"[Portal Sync] FAIL — {status_label}"
    bits: list[str] = []
    if changelog.fidelity_added:
        bits.append(f"{len(changelog.fidelity_added)} fidelity")
    if changelog.qianji_added_count > 0:
        bits.append(f"{changelog.qianji_added_count} qianji")
    if changelog.empower_added > 0:
        bits.append(f"{changelog.empower_added} empower")
    if changelog.net_worth_delta is not None:
        bits.append(f"nw {_fmt_delta(changelog.net_worth_delta)}")
    if not bits:
        return "[Portal Sync] OK"
    return "[Portal Sync] OK — " + ", ".join(bits)
