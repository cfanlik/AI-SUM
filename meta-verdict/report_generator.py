"""
meta-verdict 报告生成器
终端输出 + Markdown 综合雷达报
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from arbitrator import MetaResult
import config


STAGE_LABEL = {
    "CONTROLLED":   "🔒 极度控盘",
    "ACCUMULATING": "🎯 吸筹中",
    "WATCHLIST":    "👀 观察中",
    "DISTRIBUTING": "💀 派发中",
    "NEUTRAL":      "─ 中性",
}


def generate_report(
    acc_list: list[MetaResult],
    dist_list: list[MetaResult],
    all_count: int,
    scan_time: str,
) -> str:
    """终端 + MD 双输出，返回 MD 路径"""

    # ── 终端 ──
    print(f"\n{'='*75}")
    print(f"🔮 meta-verdict 综合仲裁 | {scan_time}")
    print(f"   有效代币: {all_count} | 🎯吸筹: {len(acc_list)} | 💀出货: {len(dist_list)}")
    print(f"{'='*75}")

    if acc_list:
        print(f"\n🎯 吸筹排行 Top {min(len(acc_list), config.META_TOP_N)}")
        print(f"{'代币':<8}{'链':<5}{'综合分':>6}{'阶段':<12}"
              f"{'master':<10}{'opus':>8}{'whale':<8}{'CB判定':<22}{'价格':>10}{'VWAP':>10}")
        print("-" * 100)
        for r in acc_list[:config.META_TOP_N]:
            print(f"{r.token_symbol:<8}{r.chain:<5}{r.meta_score:>6.1f}"
                  f"{STAGE_LABEL.get(r.stage, r.stage):<12}"
                  f"{r.master_signal:<10}{r.opus_score:>7.1f} "
                  f"{r.whale_level:<8}{r.cb_verdict:<22}"
                  f"${r.cb_gecko_price:>9.4f} ${r.cb_vwap:>9.4f}")

    if dist_list:
        print(f"\n💀 出货预警")
        print(f"{'代币':<8}{'链':<5}{'综合分':>6}{'阶段':<12}"
              f"{'master':<10}{'CB判定':<22}{'暴利区%':>8}{'价格':>10}")
        print("-" * 85)
        for r in dist_list[:config.META_TOP_N]:
            print(f"{r.token_symbol:<8}{r.chain:<5}{r.meta_score:>6.1f}"
                  f"{STAGE_LABEL.get(r.stage, r.stage):<12}"
                  f"{r.master_signal:<10}{r.cb_verdict:<22}"
                  f"{r.cb_windfall_pct:>7.1f}% ${r.cb_gecko_price:>9.4f}")

    # ── Markdown ──
    md = _build_md(acc_list, dist_list, all_count, scan_time)
    report_dir = Path(config.REPORT_DIR)
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    path = report_dir / f"meta_{ts}.md"
    path.write_text(md, encoding="utf-8")
    print(f"\n📄 综合报告: {path}")
    return str(path)


def _build_md(
    acc_list: list[MetaResult],
    dist_list: list[MetaResult],
    all_count: int,
    scan_time: str,
) -> str:
    lines = [
        f"# 🔮 meta-verdict 五引擎综合仲裁 — {scan_time}",
        "",
        f"> 有效代币: **{all_count}** | 🎯吸筹: **{len(acc_list)}** | 💀出货: **{len(dist_list)}**",
        ">",
        "> **数据来源**: master-scan / opus-scan / unified-scan / whale-scan / cost-basis-scan",
        "> **仲裁方式**: 5引擎加权积分 → 综合排名（≥3分=吸筹 / ≤-2分=出货）",
        "",
        "---",
        "",
    ]

    # ── 积分权重说明 ──
    lines += [
        "## 📊 积分权重体系",
        "",
        "| 引擎 | 信号 | 积分 |",
        "|------|------|------|",
        "| master-scan | DIAMOND / RED / YELLOW | +4 / +2 / +1 |",
        "| opus-scan | acc_confidence × 0.04 — dist_confidence × 0.04 | 动态 |",
        "| unified-scan | DIAMOND / RED / YELLOW | +4 / +2 / +1 |",
        "| whale-scan | HIGH / MEDIUM / LOW | +3 / +2 / +1 |",
        "| cost-basis-scan | SQUEEZE_ACC_HIGH/STEALTH_ACC / DEATH_SPIRAL / LIQUIDITY_CRISIS | +3 / -5 / -3 |",
        "",
        "---",
        "",
    ]

    # ── 吸筹排行 ──
    if acc_list:
        lines.append(f"## 🎯 吸筹排行 — {len(acc_list)} 个")
        lines.append("")
        lines.append("> **解读**: 综合积分越高，多引擎共识越强。阶段为「极度控盘」表示机构锁仓控盘；「吸筹中」表示正在建仓。")
        lines.append("")
        lines.append("| 排名 | 代币 | 链 | 综合分 | 阶段 | master | opus | whale | CB判定 | 现价 | VWAP | CB信号 |")
        lines.append("|------|------|----|----|------|--------|------|-------|--------|------|------|--------|")
        for i, r in enumerate(acc_list[:config.META_TOP_N], 1):
            pnl = ""
            if r.cb_vwap > 0 and r.cb_gecko_price > 0:
                p = (r.cb_gecko_price - r.cb_vwap) / r.cb_vwap * 100
                pnl = f"+{p:.0f}%" if p >= 0 else f"{p:.0f}%"
            lines.append(
                f"| {i} | {r.token_symbol} | {r.chain} | **{r.meta_score:.1f}** "
                f"| {STAGE_LABEL.get(r.stage, r.stage)} "
                f"| {r.master_signal} | {r.opus_score:.1f} | {r.whale_level} "
                f"| {r.cb_verdict} | ${r.cb_gecko_price:.4f} | ${r.cb_vwap:.4f} "
                f"| {r.cb_signals} |"
            )
        lines.append("")

    # ── 出货预警 ──
    if dist_list:
        lines.append(f"## 💀 出货预警 — {len(dist_list)} 个")
        lines.append("")
        lines.append("> **解读**: 综合积分越低（负值越大），出货信号越强。DEATH_SPIRAL 表示派发末期随时崩盘。")
        lines.append("")
        lines.append("| 代币 | 链 | 综合分 | 阶段 | master | CB判定 | 暴利区% | 现价 | DIST% |")
        lines.append("|------|----|----|------|--------|--------|---------|------|-------|")
        for r in dist_list[:config.META_TOP_N]:
            lines.append(
                f"| {r.token_symbol} | {r.chain} | **{r.meta_score:.1f}** "
                f"| {STAGE_LABEL.get(r.stage, r.stage)} "
                f"| {r.master_signal} | {r.cb_verdict} "
                f"| {r.cb_windfall_pct:.1f}% | ${r.cb_gecko_price:.4f} "
                f"| {r.cb_dist_pct:.1f}% |"
            )
        lines.append("")

    # ── 积分明细（Top 20 吸筹）──
    if acc_list:
        lines.append("## 📋 积分明细（吸筹 Top 20）")
        lines.append("")
        lines.append("| 代币 | 综合 | master得分 | opus得分 | unified得分 | whale得分 | CB得分 | 有效引擎 |")
        lines.append("|------|------|-----------|---------|------------|----------|--------|---------|")
        for r in acc_list[:20]:
            lines.append(
                f"| {r.token_symbol} | {r.meta_score:.1f} "
                f"| {r.master_score:.0f} | {r.opus_score:.1f} "
                f"| {r.unified_score:.0f} | {r.whale_score:.0f} "
                f"| {r.cb_score:.0f} | {r.engine_hits} |"
            )
        lines.append("")

    lines.append("---")
    lines.append(f"*生成时间: {datetime.now().isoformat()}*")
    return "\n".join(lines)
