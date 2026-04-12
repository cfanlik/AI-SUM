"""
opus-scan — 报告生成
终端双榜单 + 单代币诊断 + MD 文件输出
"""
from __future__ import annotations
import os
from datetime import datetime
from typing import Optional
import config
from verdict_engine import VerdictResult, Evidence


# ── 终端颜色 (Linux ANSI) ──
G = "\033[92m"  # 绿
R = "\033[91m"  # 红
Y = "\033[93m"  # 黄
B = "\033[1m"   # 粗
E = "\033[0m"   # 重置


def print_leaderboard(results: list[VerdictResult], elapsed: float = 0):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    valid = [r for r in results if r.snap_count >= config.MIN_SNAPSHOTS]

    # MIXED 代币不进入单方向榜，单独展示
    non_mixed = [r for r in valid if r.verdict != "MIXED"]
    mixed     = sorted([r for r in valid if r.verdict == "MIXED"],
                       key=lambda r: r.acc_confidence + r.dist_confidence, reverse=True)[:5]

    top_acc  = sorted(non_mixed, key=lambda r: r.acc_confidence,  reverse=True)[:config.TOP_N]
    top_dist = sorted(non_mixed, key=lambda r: r.dist_confidence, reverse=True)[:config.TOP_N]

    sep = "=" * 90
    print(f"\n{sep}")
    print(f"  \U0001f6f0  opus-scan — 吸筹/出货双维度雷达  |  {now}")
    print(f"  扫描总数: {len(valid)}  |  MIXED: {len(mixed)}  |  耗时: {elapsed:.1f}s")
    print(sep)

    # 吸筹 Top N
    print(f"\n  {G}\U0001f7e2 吸筹 Top {config.TOP_N}（置信度降序，已排除MIXED）{E}")
    print(f"  {'排名':>4}  {'代币':<10} {'链':<5} {'置信度':>6}  {'acc数':>5} {'acc占比':>7} {'DEX真金':>7} {'CEX变化':>7} {chr(0x394)+'CEX':>6} {'LP(USD)':>10} {'阶段':<8}")
    print(f"  {'─'*105}")
    for i, r in enumerate(top_acc, 1):
        trend = _phase_icon(r.phase)
        lp_s = f"${r.lp_usd:,.0f}" if r.lp_usd else "-"
        print(f"  {i:>4}  {r.symbol:<10} {r.chain:<5} {r.acc_confidence:>5.1f}%  {r.acc_cnt:>5} {r.acc_hold_pct:>6.1f}% {r.dex_verified_pct:>6.1f}% {r.cex_delta_pct:>+6.1f}% {_cex_arrow(r.cex_delta_pct):>6} {lp_s:>10} {trend}")

    # 出货 Top N
    print(f"\n  {R}\U0001f534 出货 Top {config.TOP_N}（置信度降序，已排除MIXED）{E}")
    print(f"  {'排名':>4}  {'代币':<10} {'链':<5} {'置信度':>6}  {'出货者':>5} {'出货占比':>7} {'假鲸鱼':>5} {'CEX变化':>7} {chr(0x394)+'CEX':>6} {'LP(USD)':>10} {'48h派发':>6}")
    print(f"  {'─'*105}")
    for i, r in enumerate(top_dist, 1):
        lp_s = f"${r.lp_usd:,.0f}" if r.lp_usd else "-"
        print(f"  {i:>4}  {r.symbol:<10} {r.chain:<5} {r.dist_confidence:>5.1f}%  {r.seller_count:>5} {r.seller_hold_pct:>6.1f}% {r.fake_whale_count:>5} {r.cex_delta_pct:>+6.1f}% {_cex_arrow(r.cex_delta_pct):>6} {lp_s:>10} {r.dist_48h_count:>4}个")

    # 混合信号（需人工研判）
    if mixed:
        print(f"\n  {Y}\U0001f7e1 混合信号 Top 5（吸筹+出货并存，需人工研判）{E}")
        print(f"  {'排名':>4}  {'代币':<10} {'链':<5} {'吸筹':>6} {'出货':>6}  {'acc数':>5} {'CEX变化':>7} {chr(0x394)+'CEX':>6} {'LP(USD)':>10}")
        print(f"  {'─'*80}")
        for i, r in enumerate(mixed, 1):
            lp_s = f"${r.lp_usd:,.0f}" if r.lp_usd else "-"
            print(f"  {i:>4}  {r.symbol:<10} {r.chain:<5} {r.acc_confidence:>5.1f}% {r.dist_confidence:>5.1f}%  {r.acc_cnt:>5} {r.cex_delta_pct:>+6.1f}% {_cex_arrow(r.cex_delta_pct):>6} {lp_s:>10}")

    print(f"\n{sep}\n")



def print_single_verdict(vr: VerdictResult):
    sep = "=" * 70
    verdict_icon = {"ACCUMULATING": "🟢", "SLOW_DISTRIBUTION": "🔴", "MIXED": "🟡", "NEUTRAL": "⚪"}.get(vr.verdict, "?")
    print(f"\n{sep}")
    print(f"  🔍 {vr.symbol} ({vr.chain}) — 深度诊断")
    print(sep)
    print(f"\n  裁决: {verdict_icon} {vr.verdict} ({vr.verdict_detail})")
    print(f"        吸筹置信度: {vr.acc_confidence:.1f}%  |  出货置信度: {vr.dist_confidence:.1f}%")

    _print_evidence("出货证据链", vr.dist_evidence)
    _print_evidence("吸筹证据链", vr.acc_evidence)
    print(f"\n{sep}\n")


def _print_evidence(title: str, evidences: list[Evidence]):
    print(f"\n  ── {title} ──")
    for ev in evidences:
        icon = "✅" if ev.matched else "❌"
        print(f"  {icon} {ev.detail:<45} [权重{ev.weight}, {'命中' if ev.matched else '未命中'}]")


PHASE_CN = {
    "accelerating": "↑↑↑加速",
    "early_acc":    "↑↑吸筹",
    "plateau":      "→平台期",
    "topping":      "↗触顶",
    "distributing": "↓↓出货",
    "unknown":      "?未知",
}

def _phase_icon(phase: str) -> str:
    return PHASE_CN.get(phase, phase)


def _cex_arrow(delta: float) -> str:
    """CEX 占比变化方向箭头"""
    if delta < -3:
        return "↓流出"
    elif delta > 3:
        return "↑流入"
    return "→稳定"


# ── MD 报告 ──

def save_md_leaderboard(results: list[VerdictResult]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    path = os.path.join(config.REPORT_DIR, f"opus_{date_str}.md")

    valid     = [r for r in results if r.snap_count >= config.MIN_SNAPSHOTS]
    non_mixed = [r for r in valid if r.verdict != "MIXED"]
    mixed     = sorted([r for r in valid if r.verdict == "MIXED"],
                       key=lambda r: r.acc_confidence + r.dist_confidence, reverse=True)[:5]

    top_acc  = sorted(non_mixed, key=lambda r: r.acc_confidence,  reverse=True)[:config.TOP_N]
    top_dist = sorted(non_mixed, key=lambda r: r.dist_confidence, reverse=True)[:config.TOP_N]

    lines = [
        f"# opus-scan 双维度雷达报 | {now}",
        f"\n扫描总数: {len(valid)}  |  MIXED（人工研判）: {len(mixed)}",
        "\n## \U0001f7e2 吸筹 Top 10（已排除MIXED代币）\n",
        "| 排名 | 代币 | 链 | 置信度 | acc数 | acc占比 | DEX真金 | CEX变化 | \u0394CEX | LP(USD) | 阶段 |",
        "|------|------|-----|--------|-------|---------|---------|---------|------|---------|------|",
    ]
    for i, r in enumerate(top_acc, 1):
        lp_s = f"${r.lp_usd:,.0f}" if r.lp_usd else "-"
        lines.append(
            f"| {i} | {r.symbol} | {r.chain} | {r.acc_confidence:.1f}% | {r.acc_cnt} | {r.acc_hold_pct:.1f}% | {r.dex_verified_pct:.1f}% | {r.cex_delta_pct:+.1f}% | {_cex_arrow(r.cex_delta_pct)} | {lp_s} | {PHASE_CN.get(r.phase, r.phase)} |"
        )

    lines += [
        "\n## \U0001f534 出货 Top 10（已排除MIXED代币）\n",
        "| 排名 | 代币 | 链 | 置信度 | 出货者 | 出货占比 | 假鲸鱼 | CEX变化 | LP(USD) | 48h派发 |",
        "|------|------|-----|--------|--------|----------|--------|---------|---------|---------| ",
    ]
    for i, r in enumerate(top_dist, 1):
        lp_s = f"${r.lp_usd:,.0f}" if r.lp_usd else "-"
        lines.append(
            f"| {i} | {r.symbol} | {r.chain} | {r.dist_confidence:.1f}% | {r.seller_count} | {r.seller_hold_pct:.1f}% | {r.fake_whale_count} | {r.cex_delta_pct:+.1f}% | {lp_s} | {r.dist_48h_count}个 |"
        )

    if mixed:
        lines += [
            "\n## \U0001f7e1 混合信号（吸筹+出货并存，需人工研判）\n",
            "> 以下代币同时具备吸筹和出货特征，建议结合链上数据逐一核实。\n",
            "| 排名 | 代币 | 链 | 吸筹% | 出货% | acc数 | acc占比 | CEX变化 | \u0394CEX | LP(USD) |",
            "|------|------|-----|-------|-------|-------|---------|---------|------|---------|",
        ]
        for i, r in enumerate(mixed, 1):
            lp_s = f"${r.lp_usd:,.0f}" if r.lp_usd else "-"
            lines.append(
                f"| {i} | {r.symbol} | {r.chain} | {r.acc_confidence:.1f}% | {r.dist_confidence:.1f}% | {r.acc_cnt} | {r.acc_hold_pct:.1f}% | {r.cex_delta_pct:+.1f}% | {_cex_arrow(r.cex_delta_pct)} | {lp_s} |"
            )

    os.makedirs(config.REPORT_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path



def save_md_single(vr: VerdictResult) -> str:
    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    path = os.path.join(config.REPORT_DIR, f"{vr.symbol}_{date_str}.md")

    def _ev_table(evidences):
        lines = ["| 状态 | 信号 | 权重 | 详情 |", "|------|------|------|------|"]
        for ev in evidences:
            icon = "✅" if ev.matched else "❌"
            lines.append(f"| {icon} | {ev.name} | {ev.weight} | {ev.detail} |")
        return "\n".join(lines)

    verdict_cn = {"ACCUMULATING": "持续吸筹", "SLOW_DISTRIBUTION": "缓慢出货",
                  "MIXED": "吸筹与出货并存", "NEUTRAL": "平静无信号"}.get(vr.verdict, vr.verdict)

    content = f"""# {vr.symbol} ({vr.chain}) — 深度诊断报告

## 裁决: {verdict_cn}
- 吸筹置信度: **{vr.acc_confidence:.1f}%**
- 出货置信度: **{vr.dist_confidence:.1f}%**

## 出货证据链

{_ev_table(vr.dist_evidence)}

## 吸筹证据链

{_ev_table(vr.acc_evidence)}

## 关键指标

| 指标 | 值 |
|------|-----|
| 快照数 | {vr.snap_count} |
| 吸筹地址数 | {vr.acc_cnt} |
| 吸筹持仓占比 | {vr.acc_hold_pct:.1f}% |
| DEX真金率 | {vr.dex_verified_pct:.1f}% |
| CEX持仓变化 | {vr.cex_delta_pct:+.1f}% |
| 出货者数 | {vr.seller_count} |
| 出货者持仓 | {vr.seller_hold_pct:.1f}% |
| 假鲸鱼数 | {vr.fake_whale_count} |
| 48h派发者 | {vr.dist_48h_count} |
| 趋势阶段 | {PHASE_CN.get(vr.phase, vr.phase)} |
"""
    os.makedirs(config.REPORT_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path
