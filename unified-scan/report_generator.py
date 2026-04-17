# ──────────────────────────────────────────────────────────
# 信号编码速查表 (Signal Code Reference)
# ──────────────────────────────────────────────────────────
# A1(DIAMOND/RED)  — BubbleMap 吸筹标签等级
# A2(YELLOW/RED)   — 二级吸筹指标（YELLOW=中等, RED=强）
# A4(CEX流出)      — 代币从 CEX 转出到链上 → 买入持有
# D1(CEX流入)      — 代币流入 CEX → 准备卖出
# D2(出货者)       — 检测到出货行为的地址
# D3(被动漂移)     — 持仓未变但价格下跌，被动承受亏损
# S1(极端集中)     — Top 地址持仓极度集中
# S2(M/L=Nx)       — 市值/流动性比 → 越高越脆弱
# S4(V/L=x)        — 换手效率 → V/L>10 极端换手（标记不计分）
# G2(LP=$x)        — LP 流动性门控 → <$30K降级, <$10K否决
# G3(死池)         — V/L<0.01 + Vol<$100 → 否决ACC信号
# ──────────────────────────────────────────────────────────

"""
unified-scan — 报告生成（终端 + MD）
"""
from __future__ import annotations
import os
from datetime import datetime
import config
from verdict_engine import UnifiedResult

VERDICT_ICON = {
    "DIAMOND": "💎", "WHALE_DUMP": "🐋", "SLOW_DISTRIBUTION": "🔴",
    "MIXED": "🟡", "STRONG_ACC": "🟢", "MODERATE_ACC": "🔵", "NEUTRAL": "⚪",
}

VERDICT_ORDER = {
    "DIAMOND": 0, "WHALE_DUMP": 1, "SLOW_DISTRIBUTION": 2,
    "MIXED": 3, "STRONG_ACC": 4, "MODERATE_ACC": 5, "NEUTRAL": 9,
}


def print_terminal_report(results: list[UnifiedResult], elapsed: float):
    sorted_r = sorted(results, key=lambda x: VERDICT_ORDER.get(x.verdict, 9))
    signaled = [r for r in sorted_r if r.verdict != "NEUTRAL"]

    counts = {}
    for r in results:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1

    print(f"\n{'='*70}")
    print(f"  unified-scan 统一雷达报 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  扫描: {len(results)} | 有信号: {len(signaled)} | 耗时: {elapsed:.1f}s")
    parts = []
    for v in ["DIAMOND","WHALE_DUMP","SLOW_DISTRIBUTION","STRONG_ACC","MODERATE_ACC","MIXED"]:
        if counts.get(v, 0) > 0:
            parts.append(f"{VERDICT_ICON.get(v,'')} {v}: {counts[v]}")
    print(f"  {' | '.join(parts)}")
    print(f"{'='*70}")

    # 按类型分组输出
    for verdict_type in ["DIAMOND", "WHALE_DUMP", "SLOW_DISTRIBUTION",
                          "STRONG_ACC", "MODERATE_ACC", "MIXED"]:
        group = [r for r in sorted_r if r.verdict == verdict_type]
        if not group:
            continue
        icon = VERDICT_ICON.get(verdict_type, "")
        print(f"\n  {icon} {verdict_type} ({len(group)} 个)")
        print(f"  {'代币':<8} {'链':<5} {'ACC%':>5} {'DIST%':>5} {'STRUCT%':>7} {'DEX真金':>6} {'CEX':>6} {'LP($)':>10} {'V/L':>6} {'信号'}")
        print(f"  {'-'*65}")
        for r in group:
            sigs = ", ".join(r.triggered[:4])
            print(f"  {r.token_symbol:<8} {r.chain:<5} {r.acc_score:>5.1f} {r.dist_score:>5.1f} "
                  f"{r.struct_risk:>7.1f} {r.dex_verified_pct:>5.1f}% {r.cex_delta_pct:>+5.1f}% {sigs}")


def generate_md_report(results: list[UnifiedResult]) -> str:
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M")
    filename = f"unified_{ts}.md"
    path = os.path.join(config.REPORT_DIR, filename)

    sorted_r = sorted(results, key=lambda x: VERDICT_ORDER.get(x.verdict, 9))
    signaled = [r for r in sorted_r if r.verdict != "NEUTRAL"]

    counts = {}
    for r in results:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1

    lines = []
    lines.append(f"# unified-scan 统一雷达报 — {now.strftime('%Y-%m-%d %H:%M')}\n")
    parts = []
    for v in ["DIAMOND","WHALE_DUMP","SLOW_DISTRIBUTION","STRONG_ACC","MODERATE_ACC","MIXED"]:
        if counts.get(v, 0) > 0:
            parts.append(f"{VERDICT_ICON.get(v,'')} {v}: **{counts[v]}**")
    lines.append(f"> 扫描 **{len(results)}** 代币 | 有信号 **{len(signaled)}** | {' | '.join(parts)}\n")
    lines.append("---\n")

    for verdict_type in ["DIAMOND", "WHALE_DUMP", "SLOW_DISTRIBUTION",
                          "STRONG_ACC", "MODERATE_ACC", "MIXED"]:
        group = [r for r in sorted_r if r.verdict == verdict_type]
        if not group:
            continue
        icon = VERDICT_ICON.get(verdict_type, "")
        lines.append(f"## {icon} {verdict_type} — {len(group)} 个\n")
        lines.append("| 代币 | 链 | ACC% | DIST% | STRUCT% | 机构控盘 | DEX真金 | CEX变化 | LP($) | V/L | 触发信号 |")
        lines.append("|------|----:|-----:|------:|--------:|---------:|--------:|--------:|--------:|-----:|----------|")
        for r in group:
            sigs = ", ".join(r.triggered[:5])
            lines.append(
                f"| {r.token_symbol} | {r.chain} | {r.acc_score:.1f} | {r.dist_score:.1f} | "
                f"{r.struct_risk:.1f} | {r.institutional_hold:.1f}% | {r.dex_verified_pct:.1f}% | "
                f"{r.cex_delta_pct:+.1f}% | {r.lp_usd:,.0f} | {r.vl_ratio:.2f} | {sigs} |"
            )
        lines.append("")

    lines.append("---\n")
    lines.append(f"*生成时间: {now.isoformat()}*\n")

    content = "\n".join(lines)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    return path
