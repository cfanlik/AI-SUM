"""
AI-SUM V8.2 — 报告生成器
输出：
  1. 终端摘要
  2. Markdown 文件（report/v5/radar_YYYYMMDD_HHMM.md）

只展示：💎 钻石绞杀区 + 🔴 红色预警 + 📊 全库摘要 + 🏆 V4 TOP10
不展示：🟡 黄色关注、📋 Watchlist
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import config, db_loader
from pattern_detector import (
    PatternResult, LEVEL_EXTREME, LEVEL_CRITICAL,
    LEVEL_RED, LEVEL_YELLOW, LEVEL_DIAMOND,
)


# ============================================================
# 工具
# ============================================================

def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _level_emoji(level: str) -> str:
    return {
        LEVEL_DIAMOND:  "💎 DIAMOND",
        LEVEL_EXTREME:  "🔴🔴🔴 EXTREME",
        LEVEL_CRITICAL: "🔴🔴 CRITICAL",
        LEVEL_RED:      "🔴 RED",
        LEVEL_YELLOW:   "🟡 YELLOW",
    }.get(level, level or "-")


# ============================================================
# 统一行格式（8列）
# ============================================================

def _format_row(r: PatternResult, md: bool = False) -> str:
    sym   = (r.token_symbol or "?")[:10]
    chain = (r.chain or "")[:4]
    level = _level_emoji(r.composite_level)
    pat   = "+".join(r.triggered_patterns)
    inst  = f"{r.institutional_hold_v8:.1f}%"
    hw    = f"{r.hidden_whale_count}个"
    dex   = f"{r.dex_verified_pct:.1f}%"
    acc   = str(r.acc_count_new)
    lp    = f"${r.lp_usd:,.0f}" if r.lp_usd > 0 else "N/A"
    vl    = f"{r.vl_ratio:.2f}" if r.vl_ratio > 0 else "0"
    ml    = f"{r.mcap_liq_ratio:.0f}x" if r.mcap_liq_ratio > 0 else "N/A"

    if md:
        return f"| {sym} | {chain} | {level} | {pat} | {inst} | {hw} | {dex} | {acc} | {lp} | {ml} | {vl} |"
    else:
        return f"  {sym:<10} {chain:<5} {level:<22} {pat:<24} {inst:>8} {hw:>8} {dex:>8} {acc:>6} {lp:>12} {ml:>8} {vl:>6}"


def _md_table_header() -> str:
    return (
        "| 代币 | 链 | 信号级别 | 触发模式 | 机构控盘率 | 隐庄 | DEX真金率 | 吸筹数 | LP($) | FDV/LP | V/L |\n"
        "|------|----|---------|---------|-----------:|-----:|--------:|------:|--------:|-------:|-----:|"
    )


# ============================================================
# 终端输出
# ============================================================

def print_terminal_report(
    all_results: list[PatternResult],
    watchlist_stats: dict,
    top10_v4: list[dict],
    scan_start_ts: str,
) -> None:
    signaled = [r for r in all_results if r.has_signal]
    diamonds = [r for r in signaled if r.composite_level == LEVEL_DIAMOND]
    red_plus = [r for r in signaled if r.is_red_or_above and r.composite_level != LEVEL_DIAMOND]

    now = _now_str()
    print(f"\n{'='*100}")
    print(f"  🛰  AI-SUM V8.2 — 高价值代币时效雷达报  |  {now}")
    print(f"  扫描总数: {len(all_results)}  |  💎钻石: {len(diamonds)}  |  🔴红色: {len(red_plus)}")
    print(f"{'='*100}")

    def _section(title, items):
        if items:
            print(f"\n  {title} — {len(items)} 个")
            print(f"  {'代币':<10} {'链':<5} {'级别':<22} {'触发模式':<24} {'机构占比':>8} {'隐庄':>8} {'真金率':>8} {'吸筹数':>6} {'LP($)':>12} {'FDV/LP':>8} {'V/L':>6}")
            print("  " + "-" * 98)
            for r in items:
                print(_format_row(r, md=False))
        else:
            print(f"\n  {title}：无")

    _section("💎 钻石绞杀区（极低流通 + 真金吸筹）", diamonds)
    _section("🔴 红色预警", red_plus)

    print(f"\n{'='*100}\n")


# ============================================================
# Markdown 报告
# ============================================================

def generate_md_report(
    all_results: list[PatternResult],
    watchlist_items: list[dict],
    watchlist_stats: dict,
    top10_v4: list[dict],
    report_dir: str = None,
) -> str:
    if report_dir is None:
        report_dir = config.REPORT_DIR

    Path(report_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    report_path = str(Path(report_dir) / f"radar_{ts}.md")

    signaled = [r for r in all_results if r.has_signal]
    diamonds = [r for r in signaled if r.composite_level == LEVEL_DIAMOND]
    red_plus = [r for r in signaled if r.is_red_or_above and r.composite_level != LEVEL_DIAMOND]

    lines = []
    now_str = _now_str()

    # 报告头
    lines.append(f"# 🛰 高价值代币时效雷达报 — {now_str}\n")
    lines.append("> **AI-SUM V8.2** | 基于 Top300 持有者多快照横向差分分析")
    lines.append(f"> 扫描代币数: **{len(all_results)}** | 💎钻石: **{len(diamonds)}** | 🔴红色: **{len(red_plus)}**\n")

    # 💎 钻石绞杀区
    lines.append("---\n")
    lines.append(f"## 💎 钻石绞杀区（极低流通 + 真金吸筹） — {len(diamonds)} 个\n")
    if diamonds:
        lines.append(_md_table_header())
        for r in diamonds:
            lines.append(_format_row(r, md=True))
        lines.append("")
    else:
        lines.append("_本次扫描无钻石级信号_\n")

    # 🔴 红色预警
    lines.append("---\n")
    lines.append(f"## 🔴 红色预警 — {len(red_plus)} 个\n")
    if red_plus:
        lines.append(_md_table_header())
        for r in red_plus:
            lines.append(_format_row(r, md=True))
        lines.append("")
    else:
        lines.append("_本次扫描无红色预警_\n")

    # 📊 全库扫描摘要
    lines.append("---\n")
    lines.append("## 📊 全库扫描摘要\n")
    total = len(all_results)
    with_signal = len(signaled)
    lines.append(f"- 扫描代币总数：{total}")
    lines.append(f"- 有信号代币数：{with_signal}（{with_signal/max(total,1)*100:.1f}%）")
    lines.append(f"- 💎钻石：{len(diamonds)}")
    lines.append(f"- 🔴红色：{len(red_plus)}")
    lines.append("")

    # 🏆 V4 TOP10
    lines.append("---\n")
    lines.append("## 🏆 V4 综合评分背景参照（TOP 10）\n")
    lines.append("| 代币 | 链 | 吸筹均分 | 吸筹地址数 | 快照数 |")
    lines.append("|------|----|---------:|----------:|------:|")
    for row in top10_v4[:10]:
        sym   = (row.get("token_symbol") or "?")[:10]
        chain = (row.get("chain") or "")[:4]
        score = row.get("avg_acc_score") or 0
        acc_h = row.get("acc_holders") or 0
        snaps = row.get("snap_count") or 0
        lines.append(f"| {sym} | {chain} | {score:.1f} | {acc_h} | {snaps} |")
    lines.append("")

    # 页脚
    lines.append("---")
    lines.append(f"_报告生成时间: {now_str} | AI-SUM V8.2 | 双90%阈值_")

    content = "\n".join(lines)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(content)

    return report_path
