"""
src/digest.py — PURE daily digest builder and weekly summary builder.

No DB, network, or file IO. Accepts plain dicts/lists/strings and returns a
formatted LINE message string.

Public API
----------
build_daily_digest(run_date, portfolio_latest, performance_summary,
                   macro_regime, signals, dashboard_url) -> str

build_weekly_summary(rows, week_start, week_end, report_url) -> str | None
    rows: list of outcome-detail dicts from db.fetch_outcome_detail_rows.
    Returns None when rows is empty (caller skips notification).
    Note: 建玉回転 (new/exit turnover) is intentionally omitted in v1 — it
    requires portfolio_snapshots diff_type diffs, not signal outcome rows.
    Planned for Phase 4 when portfolio goes active.
"""

from __future__ import annotations

from datetime import date
from typing import Any

_SEP = "──────────"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _bias_label(market_bias: str | None) -> str:
    return {
        "risk_on": "リスクオン",
        "neutral": "中立",
        "risk_off": "リスクオフ",
    }.get(market_bias or "", "不明")


def _is_portfolio_available(portfolio_latest: dict | None) -> bool:
    if not portfolio_latest:
        return False
    return bool(
        portfolio_latest.get("available") or portfolio_latest.get("status") == "ok"
    )


def _fmt_weight(w: Any) -> str:
    try:
        return f"{float(w):.1%}"
    except (TypeError, ValueError):
        return "—"


def _fmt_pct(v: Any, precision: int = 0) -> str:
    try:
        if precision == 0:
            return f"{float(v):.0%}"
        return f"{float(v):.{precision}%}"
    except (TypeError, ValueError):
        return "—"


def _group_line(label: str, positions: list[dict]) -> str:
    """Render one group line: 'ラベル: name1 w1% / name2 w2% ほかN' or 'ラベル: なし'."""
    if not positions:
        return f"{label}: なし"
    # Sort by target_weight desc
    sorted_pos = sorted(
        positions, key=lambda p: float(p.get("target_weight") or 0), reverse=True
    )
    top2 = sorted_pos[:2]
    rest = sorted_pos[2:]
    parts = [
        f"{p.get('name', p.get('ticker', '?'))} {_fmt_weight(p.get('target_weight'))}"
        for p in top2
    ]
    line = f"{label}: " + " / ".join(parts)
    if rest:
        line += f" ほか{len(rest)}"
    return line


def _portfolio_block(portfolio_latest: dict) -> str:
    """Build the portfolio section when available."""
    mode = portfolio_latest.get("mode") or "shadow"
    mv = portfolio_latest.get("model_version") or ""
    gross = portfolio_latest.get("gross_exposure")
    vol = portfolio_latest.get("expected_vol")
    positions: list[dict] = portfolio_latest.get("positions") or []

    gross_str = _fmt_pct(gross, 0) if gross is not None else "—"
    vol_str = _fmt_pct(vol, 1) if vol is not None else "—"

    if mode == "active":
        mode_tag = f"[active / {mv}]" if mv else "[active]"
    else:
        mode_tag = f"[{mode} / {mv}]" if mv else f"[{mode}]"

    lines = [
        f"🧺 今日の建玉 {mode_tag}",
        f"グロス {gross_str} ・想定ボラ {vol_str}",
    ]

    if mode == "active":
        # active mode: separate groups for increase/decrease/hold
        grp_new = [p for p in positions if p.get("diff_type") == "new"]
        grp_inc = [p for p in positions if p.get("diff_type") == "increase"]
        grp_dec = [p for p in positions if p.get("diff_type") == "decrease"]
        grp_hold = [p for p in positions if p.get("diff_type") == "hold"]
        grp_exit = [p for p in positions if p.get("diff_type") == "exit"]

        lines.append(_group_line("新規", grp_new))
        if grp_inc:
            lines.append(_group_line("増", grp_inc))
        if grp_dec:
            lines.append(_group_line("減", grp_dec))
        if grp_hold:
            lines.append(_group_line("継続", grp_hold))
        lines.append(_group_line("手仕舞い", grp_exit))
    else:
        # shadow mode: 新規 / 継続(hold+increase+decrease) / 手仕舞い
        grp_new = [p for p in positions if p.get("diff_type") == "new"]
        grp_cont = [
            p
            for p in positions
            if p.get("diff_type") in ("hold", "increase", "decrease")
        ]
        grp_exit = [p for p in positions if p.get("diff_type") == "exit"]

        lines.append(_group_line("新規", grp_new))
        if grp_cont:
            lines.append(_group_line("継続", grp_cont))
        lines.append(_group_line("手仕舞い", grp_exit))

    return "\n".join(lines)


def _portfolio_unavailable_block(portfolio_latest: dict | None) -> str:
    reason = "データなし"
    if portfolio_latest:
        r = portfolio_latest.get("reason")
        if r:
            reason = r
    return f"🧺 本日のポートフォリオ提案なし ({reason})"


def _performance_line(performance_summary: dict | None) -> str:
    try:
        h5 = (performance_summary or {}).get("horizons", {}).get("5", {})
        count = h5.get("count", 0) or 0
        hit_rate = h5.get("hit_rate")
        avg_return = h5.get("avg_return")
        if count == 0 or hit_rate is None:
            return "🎯 直近実績: 実績蓄積中"
        avg_str = f"{avg_return:+.1%}" if avg_return is not None else "—"
        return f"🎯 直近実績(5日): 的中 {hit_rate:.0%} (n={count}) / 平均 {avg_str}"
    except Exception:  # noqa: BLE001
        return "🎯 直近実績: 実績蓄積中"


def _signal_counts(signals: list[dict]) -> tuple[int, int, int]:
    b = mb = s = 0
    for sig in signals or []:
        if not sig.get("gate_passed"):
            continue
        action = sig.get("action", "")
        if action == "BUY":
            b += 1
        elif action == "MILD_BUY":
            mb += 1
        elif action in ("SELL", "MILD_SELL"):
            s += 1
    return b, mb, s


# Digest-only operation: per-ticker pushes are off by default, so the digest is
# where the user learns WHICH tickers to act on. Names are capped per action to
# keep one LINE message comfortably under the API text limit.
_SIGNAL_NAME_GROUPS = [
    ("BUY", "🔴 買い"),
    ("MILD_BUY", "🟠 やや買い"),
    ("MILD_SELL", "🔵 やや売り"),
    ("SELL", "🟢 売り"),
]
_MAX_NAMES_PER_ACTION = 4


def _signal_name_lines(signals: list[dict]) -> list[str]:
    """One line per non-empty action group listing gate-passed ticker names."""
    lines = []
    for action, label in _SIGNAL_NAME_GROUPS:
        names = [
            sig.get("name") or sig.get("ticker", "?")
            for sig in (signals or [])
            if sig.get("gate_passed") and sig.get("action") == action
        ]
        if not names:
            continue
        line = f"{label}: " + " / ".join(names[:_MAX_NAMES_PER_ACTION])
        if len(names) > _MAX_NAMES_PER_ACTION:
            line += f" ほか{len(names) - _MAX_NAMES_PER_ACTION}件"
        lines.append(line)
    return lines


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_daily_digest(
    run_date: str,
    portfolio_latest: dict | None,
    performance_summary: dict | None,
    macro_regime: dict | None,
    signals: list[dict],
    dashboard_url: str,
) -> str:
    """Build and return the morning digest LINE message string.

    Pure function — no side effects, no IO.
    """
    # --- Header line ---
    header = f"📊 朝のダイジェスト ({run_date})"

    # --- Regime line ---
    if not macro_regime:
        regime_line = "レジーム: 不明"
    else:
        bias = macro_regime.get("market_bias")
        label = _bias_label(bias)
        usdjpy = macro_regime.get("usdjpy")
        if usdjpy is not None:
            regime_line = f"レジーム: {label} / ドル円 {float(usdjpy):.1f}"
        else:
            regime_line = f"レジーム: {label}"

    # --- Portfolio block ---
    if _is_portfolio_available(portfolio_latest):
        portfolio_section = _portfolio_block(portfolio_latest)
    else:
        portfolio_section = _portfolio_unavailable_block(portfolio_latest)

    # --- Signal counts + per-action ticker names ---
    b, mb, s = _signal_counts(signals)
    signal_line = f"📨 個別シグナル: 買い{b} / やや買い{mb} / 売り{s}"
    name_lines = _signal_name_lines(signals)

    # --- Performance line ---
    perf_line = _performance_line(performance_summary)

    # --- Assemble ---
    parts = [
        header,
        regime_line,
        _SEP,
        portfolio_section,
        _SEP,
        signal_line,
        *name_lines,
        perf_line,
        f"詳細: {dashboard_url}",
    ]
    return "\n".join(parts)


def build_weekly_summary(
    rows: list[dict],
    week_start: str,
    week_end: str,
    report_url: str,
) -> str | None:
    """Build and return a weekly realized-performance LINE summary string.

    Pure function — no side effects, no IO.

    Returns None when rows is empty (caller should skip notification).

    Note: 建玉回転 (new/exit position turnover counts) is intentionally omitted
    in v1.  That metric requires portfolio_snapshots diff_type diffs, which are
    not present in fetch_outcome_detail_rows (signal-outcome rows only).
    Planned for Phase 4 once the portfolio moves to active mode.
    """
    if not rows:
        return None

    # --- Date header (no leading zeros) ---
    try:
        ws = date.fromisoformat(week_start)
        we = date.fromisoformat(week_end)
        date_range = f"{ws.month}/{ws.day}〜{we.month}/{we.day}"
    except (ValueError, TypeError):
        date_range = f"{week_start}〜{week_end}"

    header = f"📈 週間実績 ({date_range})"

    # --- Signal counts ---
    total = len(rows)
    buy_actions = {"BUY", "MILD_BUY"}
    sell_actions = {"SELL", "MILD_SELL"}
    buy_count = sum(1 for r in rows if r.get("action") in buy_actions)
    sell_count = sum(1 for r in rows if r.get("action") in sell_actions)
    signal_line = f"シグナル: {total}件 (買い系{buy_count} / 売り系{sell_count})"

    # --- Performance line ---
    hit_rows = [r for r in rows if r.get("hit") is not None]
    if hit_rows:
        hit_rate = sum(1 for r in hit_rows if r.get("hit") is True) / len(hit_rows)
        hit_str = f"{hit_rate:.0%}"
    else:
        hit_str = "—"

    ret_rows = [r for r in rows if r.get("realized_ret") is not None]
    if ret_rows:
        avg_ret = sum(r["realized_ret"] for r in ret_rows) / len(ret_rows)
        avg_str = f"{avg_ret:+.1%}"
    else:
        avg_str = "—"

    perf_line = f"的中率(5日): {hit_str} / 平均 {avg_str}"

    excess_rows = [r for r in rows if r.get("excess_ret") is not None]
    if excess_rows:
        avg_excess = sum(r["excess_ret"] for r in excess_rows) / len(excess_rows)
        perf_line += f" / 対TOPIX {avg_excess:+.1%}"

    # --- Best / Worst line (only when at least one realized_ret exists) ---
    best_worst_line: str | None = None
    if ret_rows:
        best = max(ret_rows, key=lambda r: r["realized_ret"])
        worst = min(ret_rows, key=lambda r: r["realized_ret"])
        best_name = best.get("name") or best.get("ticker", "?")
        worst_name = worst.get("name") or worst.get("ticker", "?")
        best_worst_line = (
            f"ベスト: {best_name} {best['realized_ret']:+.1%}"
            f" ・ワースト: {worst_name} {worst['realized_ret']:+.1%}"
        )

    # --- Assemble ---
    parts = [header, signal_line, perf_line]
    if best_worst_line:
        parts.append(best_worst_line)
    if report_url:
        parts.append(f"レポート: {report_url}")

    return "\n".join(parts)
