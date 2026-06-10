"""
src/digest.py — PURE daily digest builder.

No DB, network, or file IO. Accepts plain dicts/lists/strings and returns a
formatted LINE message string.

Public API
----------
build_daily_digest(run_date, portfolio_latest, performance_summary,
                   macro_regime, signals, dashboard_url) -> str
"""

from __future__ import annotations

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
    return bool(portfolio_latest.get("available") or portfolio_latest.get("status") == "ok")


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
    sorted_pos = sorted(positions, key=lambda p: float(p.get("target_weight") or 0), reverse=True)
    top2 = sorted_pos[:2]
    rest = sorted_pos[2:]
    parts = [f"{p.get('name', p.get('ticker', '?'))} {_fmt_weight(p.get('target_weight'))}"
             for p in top2]
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
        grp_cont = [p for p in positions
                    if p.get("diff_type") in ("hold", "increase", "decrease")]
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
    for sig in (signals or []):
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

    # --- Signal counts ---
    b, mb, s = _signal_counts(signals)
    signal_line = f"📨 個別シグナル: 買い{b} / やや買い{mb} / 売り{s}"

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
        perf_line,
        f"詳細: {dashboard_url}",
    ]
    return "\n".join(parts)
