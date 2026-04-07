"""
AI-SUM V5 — 报告生成器
输出：
  1. 终端彩色摘要（实时可读）
  2. Markdown 文件（report/v5/YYYYMMDD_HHMM.md）
"""
from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from pathlib import Path

from . import config, db_loader
from .pattern_detector import PatternResult, LEVEL_EXTREME, LEVEL_CRITICAL, LEVEL_RED, LEVEL_YELLOW


# ============================================================
# 工具
# ============================================================

def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _level_emoji(level: str) -> str:
    return {
        LEVEL_EXTREME:  "🔴🔴🔴 EXTREME",
        LEVEL_CRITICAL: "🔴🔴 CRITICAL",
        LEVEL_RED:      "🔴 RED",
        LEVEL_YELLOW:   "🟡 YELLOW",
    }.get(level, level or "-")


def _pct(v: float, decimals: int = 1) -> str:
    return f"{v * 100:.{decimals}f}%"


def _fmt_turnover(v: float) -> str:
    return f"{v * 100:.1f}%"


# ============================================================
# 报告行格式化
# ============================================================

def _format_signal_row(r: PatternResult, md: bool = False) -> str:
    sym     = (r.token_symbol or "?")[:10]
    chain   = (r.chain or "")[:4]
    level   = _level_emoji(r.composite_level)
    pat     = "+".join(r.triggered_patterns)
    turn    = _fmt_turnover(r.roster_turnover_pct)
    new_acc = str(r.new_acc_count)
    ob_pct  = _pct(r.latest_only_buy_pct)
    d_hold  = f"{r.delta_acc_hold:+.2f}%"
    gap_w   = " ⚠️时差大" if r.gap_warning else ""

    if md:
        return f"| {sym} | {chain} | {level} | {pat} | {turn}{gap_w} | {new_acc} | {ob_pct} | {d_hold} |"
    else:
        return f"  {sym:<10} {chain:<4} {level:<16} {pat:<22} {turn:<8} {new_acc:>6} {ob_pct:>8} {d_hold:>8}{gap_w}"


# ============================================================
# 终端输出
# ============================================================

def print_terminal_report(
    all_results: list[PatternResult],
    watchlist_stats: dict,
    top10_v4: list[dict],
    scan_start_ts: str,
) -> None:
    """向终端输出简洁的雷达报。"""
    signaled = [r for r in all_results if r.has_signal]
    red_plus  = [r for r in signaled if r.is_red_or_above]
    yellows   = [r for r in signaled if not r.is_red_or_above]

    now = _now_str()
    print(f"\n{'='*90}")
    print(f"  🛰  AI-SUM V5 — 高价值代币时效雷达报  |  {now}")
    print(f"  扫描总数: {len(all_results)}  |  红色预警: {len(red_plus)}  |  黄色关注: {len(yellows)}")
    print(f"  Watchlist: 新增 {watchlist_stats['new_added']} / 更新 {watchlist_stats['updated']} / EXPIRED {watchlist_stats['expired']} / ACTIVE {watchlist_stats['total_active']}")
    print(f"{'='*90}")

    # 红色+
    if red_plus:
        print(f"\n  🔴 红色预警（立即关注）— {len(red_plus)} 个")
        print(f"  {'代币':<10} {'链':<4} {'级别':<16} {'触发模式':<22} {'换手率':<8} {'新acc':>6} {'只买不卖':>8} {'Δ持仓':>8}")
        print("  " + "-" * 88)
        for r in red_plus:
            print(_format_signal_row(r, md=False))
    else:
        print("\n  🔴 红色预警：无")

    # 黄色
    if yellows:
        print(f"\n  🟡 黄色关注（持续观察）— {len(yellows)} 个")
        print(f"  {'代币':<10} {'链':<4} {'级别':<16} {'触发模式':<22} {'换手率':<8} {'新acc':>6} {'只买不卖':>8} {'Δ持仓':>8}")
        print("  " + "-" * 88)
        for r in yellows[:20]:  # 最多展示 20 个
            print(_format_signal_row(r, md=False))
        if len(yellows) > 20:
            print(f"  ... 共 {len(yellows)} 个，仅展示前 20 个，完整见 MD 报告")

    print(f"\n{'='*90}\n")


# ============================================================
# Markdown 报告
# ============================================================

def _md_table_header() -> str:
    return (
        "| 代币 | 链 | 信号级别 | 触发模式 | 换手率(12h) | 新acc地址 | 只买不卖% | Δ持仓% |\n"
        "|------|----|---------|---------|-----------:|----------:|----------:|-------:|"
    )


def generate_md_report(
    all_results: list[PatternResult],
    watchlist_items: list[dict],
    watchlist_stats: dict,
    top10_v4: list[dict],
    report_dir: str = None,
) -> str:
    """
    生成完整 Markdown 报告。
    返回报告文件路径。
    """
    if report_dir is None:
        report_dir = config.REPORT_DIR

    Path(report_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    report_path = str(Path(report_dir) / f"radar_{ts}.md")

    signaled  = [r for r in all_results if r.has_signal]
    red_plus  = [r for r in signaled if r.is_red_or_above]
    yellows   = [r for r in signaled if not r.is_red_or_above]

    lines = []
    now_str = _now_str()

    # 报告头
    lines.append(f"# 🛰 高价值代币时效雷达报 — {now_str}\n")
    lines.append("> **AI-SUM V5** | 基于 Top300 持有者多快照横向差分分析")
    lines.append(f"> 扫描代币数: **{len(all_results)}** | 红色预警: **{len(red_plus)}** | 黄色关注: **{len(yellows)}** | Watchlist ACTIVE: **{watchlist_stats.get('total_active', 0)}**\n")

    # 红色预警
    lines.append("---\n")
    lines.append(f"## 🔴 红色预警（立即关注） — {len(red_plus)} 个\n")
    if red_plus:
        lines.append(_md_table_header())
        for r in red_plus:
            lines.append(_format_signal_row(r, md=True))
        lines.append("")
        # 详细展开
        for r in red_plus:
            lines.append(f"### {r.token_symbol} ({r.chain})")
            lines.append(f"- **地址**: `{r.token_address}`")
            lines.append(f"- **综合信号**: {_level_emoji(r.composite_level)}")
            lines.append(f"- **快照时间**: {r.latest_snapshot}（共 {r.snap_count} 次快照）")
            if r.gap_warning:
                lines.append(f"- ⚠️ **时间间隔过大**，参考价值降低")
            if r.pattern_a_level:
                d = r.pattern_a_detail
                lines.append(f"- **模式A 地址聚合**: 换手率 {d.get('roster_turnover_pct', 0):.1f}%，新吸筹 {d.get('new_acc_count', 0)} 个")
            if r.pattern_b_level:
                d = r.pattern_b_detail
                lines.append(f"- **模式B 新鲸下场**: 新acc均分 {d.get('new_acc_avg_score', 0):.1f}，持仓合计 {d.get('new_acc_hold_sum_pct', 0):.2f}%")
            if r.pattern_c_level:
                d = r.pattern_c_detail
                lines.append(f"- **模式C 爆发前静默**: {d.get('conditions_met', 0)}/4 条件满足，只买不卖 {d.get('only_buy_pct', 0):.1f}%")
            lines.append("")
    else:
        lines.append("_本次扫描无红色预警_\n")

    # 黄色关注
    lines.append("---\n")
    lines.append(f"## 🟡 黄色关注（持续观察） — {len(yellows)} 个\n")
    if yellows:
        lines.append(_md_table_header())
        for r in yellows:
            lines.append(_format_signal_row(r, md=True))
        lines.append("")
    else:
        lines.append("_本次扫描无黄色关注_\n")

    # Watchlist
    lines.append("---\n")
    lines.append(f"## 📋 Watchlist 活跃追踪 — {len(watchlist_items)} 个 ACTIVE\n")
    if watchlist_items:
        lines.append("| 代币 | 链 | 级别 | 触发模式 | 进入时间 | 连续无信号 | 状态 |")
        lines.append("|------|----|----|---------|---------|----------:|-----|")
        for item in watchlist_items:
            sym     = (item.get("token_symbol") or "?")[:10]
            chain   = (item.get("chain") or "")[:4]
            level   = _level_emoji(item.get("signal_level") or "")
            pattern = (item.get("trigger_pattern") or "")[:20]
            added   = (item.get("added_at") or "")[:16]
            no_sig  = item.get("consecutive_no_signal") or 0
            status  = item.get("status") or ""
            lines.append(f"| {sym} | {chain} | {level} | {pattern} | {added} | {no_sig} | {status} |")
        lines.append("")
    else:
        lines.append("_Watchlist 当前为空_\n")

    # 全库扫描摘要
    lines.append("---\n")
    lines.append("## 📊 全库扫描摘要\n")
    total = len(all_results)
    with_signal = len(signaled)
    lines.append(f"- 扫描代币总数：{total}")
    lines.append(f"- 有信号代币数：{with_signal}（{with_signal/max(total,1)*100:.1f}%）")
    lines.append(f"- 红色及以上：{len(red_plus)}")
    lines.append(f"- 黄色关注：{len(yellows)}")
    lines.append(f"- 本次新增 Watchlist：{watchlist_stats.get('new_added', 0)}")
    lines.append(f"- Watchlist EXPIRED：{watchlist_stats.get('expired', 0)}")
    lines.append("")

    # V4 TOP10 背景参照
    lines.append("---\n")
    lines.append("## 🏆 V4 综合评分背景参照（TOP 10）\n")
    lines.append("| 代币 | 链 | 吸筹均分 | 吸筹地址数 | 快照数 |")
    lines.append("|------|----|---------:|----------:|------:|")
    for row in top10_v4[:10]:
        sym    = (row.get("token_symbol") or "?")[:10]
        chain  = (row.get("chain") or "")[:4]
        score  = row.get("avg_acc_score") or 0
        acc_h  = row.get("acc_holders") or 0
        snaps  = row.get("snap_count") or 0
        lines.append(f"| {sym} | {chain} | {score:.1f} | {acc_h} | {snaps} |")
    lines.append("")

    # 页脚
    lines.append("---")
    lines.append(f"_报告生成时间: {now_str} | AI-SUM V5 | 阈值配置: 换手黄={config.PATTERN_A_ROSTER_YELLOW*100:.0f}% 红={config.PATTERN_A_ROSTER_RED*100:.0f}%_")

    content = "\n".join(lines)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(content)

    return report_path
