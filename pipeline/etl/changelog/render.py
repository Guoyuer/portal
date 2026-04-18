"""Email body templating for the sync changelog.

Per-section ``_render_*`` helpers each emit a list of lines; :func:`format_text`
stitches them together into the plain-text body; :func:`format_html` wraps the
same text in a ``<pre>`` block with a colored status header. :func:`build_subject`
produces the short subject line.

All category-specific rules (LARGE MOVE thresholds, component-sum drift check,
exit-gate labels, money/qty formatting) live in :mod:`categorize`; this module
is layout only.
"""
from __future__ import annotations

from typing import Any

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


def _render_header(context: dict[str, Any]) -> list[str]:
    """Timestamp, status, and — on failure — exit code, error, and which
    pipeline gate blocked. Ends with a blank line.

    On success the email's Changes section speaks for itself, so there's no
    dedicated "sync did / didn't run" note. On failure we surface the gate
    name inline here instead of in a separate D1 Sync section — the
    "sync didn't execute" framing is implicit from a non-OK Status + gate.
    """
    lines: list[str] = []
    timestamp = context.get("timestamp", "")
    lines.append(f"Portal Sync Report  {timestamp}".rstrip())
    lines.append("")
    status_label = context.get("status_label", "OK")
    lines.append(f"Status: {status_label}")
    exit_code = int(context.get("exit_code", 0) or 0)
    if exit_code != 0:
        lines.append(f"Exit code: {exit_code}")
        lines.append(f"Blocked at: {_gate_for_exit(exit_code)}")
        error = context.get("error")
        if error:
            lines.append(f"Error: {error}")
    lines.append("")
    return lines


def _render_changes(changelog: SyncChangelog) -> list[str]:
    """The "Changes" section — user-facing delta by data source.

    Emits "(no changes detected)" when the changelog represents a no-op run,
    preserving the prior output shape so callers can grep for that marker.
    """
    lines: list[str] = ["Changes"]
    any_changes = False

    if changelog.fidelity_added:
        any_changes = True
        lines.append(f"  * Fidelity: +{len(changelog.fidelity_added)} transaction(s)")
        for run_date, action_type, symbol, qty, amount in changelog.fidelity_added:
            sym = symbol or "-"
            qty_str = _fmt_qty(qty) if qty else ""
            qty_part = f"  {qty_str} share(s)" if qty_str else ""
            lines.append(
                f"      {run_date}  {action_type.upper():<5} {sym:<6}{qty_part}   {_fmt_delta(amount)}"
            )

    if changelog.qianji_added_count > 0:
        any_changes = True
        total = sum(tot for _c, tot in changelog.qianji_added_by_category.values())
        lines.append(
            f"  * Qianji: +{changelog.qianji_added_count} record(s) ({_fmt_money(total)} total)"
        )
        for cat, (count, tot) in sorted(changelog.qianji_added_by_category.items()):
            label = cat or "(uncategorized)"
            avg = tot / count if count else 0.0
            lines.append(
                f"      {label}: {_fmt_money(tot)}  ({count} record(s), avg {_fmt_money(avg)})"
            )
            # Expand low-count categories (1-2 rows) with per-row date + note —
            # at this volume the aggregate tells the user nothing, but the
            # date lets them verify e.g. "did my paycheck land on the expected
            # Friday?" at a glance. Larger categories stay compact.
            if count <= 2:
                for date, amount, note in changelog.qianji_added_rows_by_category.get(cat, []):
                    note_part = f'  "{note}"' if note else ""
                    lines.append(f"          {date}  {_fmt_money(amount)}{note_part}")

    if changelog.daily_close_added > 0:
        any_changes = True
        through = changelog.daily_close_max_after or "?"
        lines.append(
            f"  * Prices: {changelog.daily_close_added} new close row(s); through {through}"
        )

    if changelog.econ_refreshed:
        # Only surface FRED when the key set *changed* (added/removed series).
        # Stable runs skip this block entirely — see diff() for the rule.
        any_changes = True
        if changelog.econ_keys_added:
            names = ", ".join(changelog.econ_keys_added)
            n = len(changelog.econ_keys_added)
            lines.append(f"  * FRED: +{n} new indicator(s) ({names})")
        if changelog.econ_keys_removed:
            names = ", ".join(changelog.econ_keys_removed)
            n = len(changelog.econ_keys_removed)
            lines.append(f"  * FRED: -{n} indicator(s) removed ({names})")

    if changelog.empower_added > 0:
        any_changes = True
        suffix = ""
        # Attach $ delta when both endpoints have a value. When only AFTER
        # is set (first ever snapshot), show the opening balance instead.
        if changelog.empower_value_delta is not None:
            suffix = f"  ({_fmt_delta(changelog.empower_value_delta)})"
        elif changelog.empower_value_after is not None:
            suffix = f"  (opening balance {_fmt_money(changelog.empower_value_after)})"
        lines.append(
            f"  * Empower: +{changelog.empower_added} 401k snapshot(s){suffix}"
        )

    if not any_changes:
        lines.append("  (no changes detected)")
    lines.append("")
    return lines


def _render_net_worth(changelog: SyncChangelog) -> list[str]:
    """Net-worth block with component breakdown, LARGE MOVE flag, and
    component-sum consistency check.

    Cases preserved from the original implementation:
      1) both endpoints present AND delta is meaningful → full block
      2) both endpoints present, same date, zero delta → single 'Unchanged' line
      3) only one endpoint present → show what we have with a prior-snapshot hint
    """
    nw_before = changelog.net_worth_before
    nw_after = changelog.net_worth_after
    pt_before = changelog.net_worth_point_before
    pt_after = changelog.net_worth_point_after
    before_date = changelog.net_worth_before_date or ""
    after_date = changelog.net_worth_after_date or ""

    lines: list[str] = []
    if nw_before is not None and nw_after is not None:
        same_date = bool(before_date) and before_date == after_date
        delta_is_zero = (
            changelog.net_worth_delta is None
            or abs(changelog.net_worth_delta) < 0.01
        )
        delta = changelog.net_worth_delta
        pct = changelog.net_worth_delta_pct()
        is_large = delta is not None and (
            abs(delta) >= _LARGE_MOVE_USD
            or (pct is not None and abs(pct) >= _LARGE_MOVE_PCT)
        )
        header = "Net Worth"
        if is_large:
            header += "  [LARGE MOVE]"
        lines.append(header)

        if same_date and delta_is_zero:
            lines.append(f"  Unchanged — {after_date}: {_fmt_money(nw_after)}")
        else:
            pct_str = f" / {pct:+.2f}%" if pct is not None else ""
            lines.append(f"  {before_date}  →  {after_date}")
            if delta is not None:
                # Top line: running totals + delta + pct
                lines.append(
                    _render_component_row("Total:", nw_before, nw_after).rstrip(")")
                    + f"{pct_str})"
                )
                if pt_before is not None and pt_after is not None:
                    lines.append(_render_component_row("US Equity:", pt_before.us_equity, pt_after.us_equity))
                    lines.append(_render_component_row("Non-US:", pt_before.non_us_equity, pt_after.non_us_equity))
                    lines.append(_render_component_row("Crypto:", pt_before.crypto, pt_after.crypto))
                    lines.append(_render_component_row("Safe Net:", pt_before.safe_net, pt_after.safe_net))
                    lines.append(_render_component_row("Liabilities:", pt_before.liabilities, pt_after.liabilities))
                    after_check = _component_check_line(pt_after)
                    if after_check:
                        lines.append(after_check)
        lines.append("")
    elif nw_after is not None:
        lines.append("Net Worth")
        lines.append(f"  {after_date}: {_fmt_money(nw_after)}  (no prior snapshot)")
        if pt_after is not None:
            check = _component_check_line(pt_after)
            if check:
                lines.append(check)
        lines.append("")
    elif nw_before is not None:
        lines.append("Net Worth")
        lines.append(f"  {before_date}: {_fmt_money(nw_before)}  (no prior snapshot)")
        lines.append("")
    return lines


def _render_warnings(context: dict[str, Any]) -> list[str]:
    """'Warnings (from validation)' list, or empty when none."""
    warnings = context.get("warnings") or []
    if not warnings:
        return []
    lines: list[str] = ["Warnings (from validation)"]
    for w in warnings:
        lines.append(f"  * {w}")
    lines.append("")
    return lines


def format_text(changelog: SyncChangelog, context: dict[str, Any]) -> str:
    """Plain-text email body, assembled from per-section helpers."""
    lines: list[str] = []
    lines.extend(_render_header(context))
    lines.extend(_render_changes(changelog))
    lines.extend(_render_net_worth(changelog))
    lines.extend(_render_warnings(context))

    log_file = context.get("log_file", "")
    if log_file:
        lines.append(f"Log: {log_file}")
    duration = context.get("duration", "")
    if duration:
        lines.append(f"Duration: {duration}")
    return "\n".join(lines)


def format_html(changelog: SyncChangelog, context: dict[str, Any]) -> str:
    """HTML email body. Simple table-less layout with monospace blocks."""
    # Rather than duplicate the whole rendering, wrap the text version in
    # <pre> so spacing stays predictable in Gmail. Add a minimal header.
    text = format_text(changelog, context)
    safe = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    status_label = context.get("status_label", "OK")
    exit_code = context.get("exit_code", 0)
    color = "#2e7d32" if exit_code == 0 else "#c62828"
    return (
        "<html><body style=\"font-family: -apple-system, Segoe UI, sans-serif; color: #222;\">"
        f"<h2 style=\"color: {color}; margin-bottom: 8px;\">Portal Sync — {status_label}</h2>"
        f"<pre style=\"font-family: Consolas, Menlo, monospace; font-size: 13px; "
        f"background: #f6f8fa; padding: 14px 16px; border-radius: 6px; "
        f"white-space: pre-wrap; line-height: 1.45;\">{safe}</pre>"
        "</body></html>"
    )


def build_subject(changelog: SyncChangelog, exit_code: int) -> str:
    """Short, informative subject line.

    Successful syncs with changes → summary of counts. Failures → prominent
    [FAIL] tag + exit code.
    """
    if exit_code != 0:
        return f"[Portal Sync] FAIL (exit {exit_code})"

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
