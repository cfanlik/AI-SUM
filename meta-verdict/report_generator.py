"""
meta-verdict 报告生成器
终端输出 + Markdown 综合雷达报（含趋势+健康+叙事）
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from arbitrator import MetaResult
from trend_analyzer import TrendReport
import config


STAGE_LABEL = {
    "CONTROLLED":   "🔒 极度控盘",
    "ACCUMULATING": "🎯 吸筹中",
    "WATCHLIST":    "👀 观察中",
    "DISTRIBUTING": "💀 派发中",
    "NEUTRAL":      "─ 中性",
}


def format_price(val) -> str:
    if val is None:
        return "—"
    try:
        val = float(val)
    except (ValueError, TypeError):
        return "—"
    if val == 0:
        return "0"
    if val >= 1:
        return f"{val:.2f}"
    if val >= 0.01:
        return f"{val:.4f}"
    s = f"{val:.10f}".rstrip('0')
    if s.endswith('.'):
        s = s[:-1]
    if s == "0" or float(s) == 0:
        return f"{val:.2e}"
    return s


def generate_report(
    acc_list: list[MetaResult],
    dist_list: list[MetaResult],
    all_count: int,
    scan_time: str,
    trend: TrendReport = None,
    health: list[dict] = None,
    conflicts: list = None,
) -> str:
    """终端 + MD 双输出"""

    # ── 终端概览 ──
    print(f"\n{'='*75}")
    print(f"🔮 meta-verdict 综合仲裁 | {scan_time}")
    print(f"   有效代币: {all_count} | 🎯吸筹: {len(acc_list)} | 💀出货: {len(dist_list)}")
    print(f"{'='*75}")

    # ── 终端: 引擎健康 ──
    if health:
        print(f"\n🏥 引擎健康")
        for h in health:
            print(f"  {h['status']} {h['engine']}: {h['detail']}")

    # ── 终端: 趋势摘要 ──
    if trend and trend.has_prev:
        print(f"\n📈 趋势 (vs {trend.prev_scan_time})")
        print(f"  🆕 新进: {len(trend.newcomers)} | 🔚 退出: {len(trend.exits)}")
        print(f"  ⬆ 上升: {trend.score_up} | ⬇ 下降: {trend.score_down} | → 稳定: {trend.stable}")
        if trend.jumps:
            for j in trend.jumps:
                sign = "+" if j["delta"] > 0 else ""
                print(f"  ⚡ {j['symbol']}: {j['prev']:.1f}→{j['curr']:.1f} ({sign}{j['delta']:.1f}) [{j['cause']}]")
        if trend.newcomers:
            names = ", ".join(f"{n['symbol']}({n['score']:.1f})" for n in trend.newcomers[:5])
            print(f"  🆕 {names}")

    # ── 终端: 吸筹排行 ──
    if acc_list:
        print(f"\n🎯 吸筹排行 Top {min(len(acc_list), config.META_TOP_N)}")
        # 带 delta 列
        print(f"{'代币':<8}{'链':<5}{'综合分':>6}{'Δ':>5}{'阶段':<12}"
              f"{'master':<10}{'opus':>8}{'whale':<8}{'CB判定':<22}{'价格':>10}{'VWAP':>10}")
        print("-" * 110)
        # 构建上轮分数 map
        prev_map = {}
        if trend and trend.has_prev:
            for sc in trend.score_changes:
                prev_map[sc["symbol"]] = sc["delta"]
        for r in acc_list[:config.META_TOP_N]:
            d = prev_map.get(r.token_symbol)
            delta_str = f"{d:+.1f}" if d is not None else " new" if any(
                n["symbol"] == r.token_symbol for n in (trend.newcomers if trend else [])
            ) else ""
            print(f"{r.token_symbol:<8}{r.chain:<5}{r.meta_score:>6.1f}{delta_str:>5}"
                  f"{STAGE_LABEL.get(r.stage, r.stage):<12}"
                  f"{r.master_signal:<10}{r.opus_score:>7.1f} "
                  f"{r.whale_level:<8}{r.cb_verdict:<22}"
                  f"${format_price(r.cb_gecko_price):>9} ${format_price(r.cb_vwap):>9}")

    # ── 终端: 出货预警 ──
    if dist_list:
        print(f"\n💀 出货预警 — {len(dist_list)} 个")
        for r in dist_list[:config.META_TOP_N]:
            print(f"  {r.token_symbol:<8} {r.meta_score:>5.1f} {r.stage:<12} "
                  f"unified={r.unified_signal} cb={r.cb_verdict}")

    # ── 终端: 矛盾 ──
    if conflicts:
        print(f"\n⚠ 引擎矛盾 — {len(conflicts)} 个")
        for c in conflicts[:10]:
            print(f"  [{c.rule}] {c.symbol:<10} {c.detail}")

    # ── Markdown ──
    md = _build_md(acc_list, dist_list, all_count, scan_time, trend, health, conflicts)
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
    trend: TrendReport = None,
    health: list[dict] = None,
    conflicts: list = None,
) -> str:
    lines = [
        f"# 🔮 meta-verdict 五引擎综合仲裁 — {scan_time}",
        "",
        f"> 有效代币: **{all_count}** | 🎯吸筹: **{len(acc_list)}** | 💀出货: **{len(dist_list)}**",
        "",
        "---",
        "",
    ]

    # ── 0. 置信度梯队与旗舰信号第一屏概览 ──
    tier_counts = {"L1-Alpha": 0, "L1-Special": 0, "L1-Alpha-Unverified": 0, "L2-Bet": 0, "L3-Watch": 0, "DENIED": 0}
    for r in acc_list + dist_list:
        t = getattr(r, "confidence_tier", "L3-Watch")
        tier_counts[t] = tier_counts.get(t, 0) + 1

    l1_alphas = [r for r in acc_list if getattr(r, "confidence_tier", "") in ("L1-Alpha", "L1-Special", "L1-Alpha-Unverified")]
    
    lines += [
        "## 💎 置信度等级与共振概览 (v2.2)",
        "",
        f"> **L1-Alpha (旗舰共振)**: `{tier_counts.get('L1-Alpha', 0)}` | "
        f"**L1-Special (特异博弈)**: `{tier_counts.get('L1-Special', 0)}` | "
        f"**L1-Unverified (新币共振)**: `{tier_counts.get('L1-Alpha-Unverified', 0)}` | "
        f"**L2-Bet**: `{tier_counts.get('L2-Bet', 0)}` | "
        f"**L3-Watch**: `{tier_counts.get('L3-Watch', 0)}`",
        ""
    ]

    if l1_alphas:
        lines += [
            "### 🚀 L1 级核心聚焦标的",
            "",
            "| 代币 | 梯队 | 综合分 | 共振 | 抗跌韧性(Norm) | 现价 | 核心特征 |",
            "|------|------|--------|------|----------------|------|----------|",
        ]
        for r in l1_alphas[:10]:
            star = " ⭐5" if r.engine_hits >= 5 else ""
            res_norm = f"{r.resilience_norm:.2f}" if getattr(r, "resilience_norm", None) is not None else "—"
            feat = []
            if getattr(r, "confidence_tier", "") == "L1-Special":
                feat.append("⚡特异形态")
            if r.cb_verdict in ("DEATH_SPIRAL", "SQUEEZE_ACC_HIGH"):
                feat.append("脱水反转")
            feat_str = " / ".join(feat) if feat else "强共振吸筹"
            lines.append(
                f"| **{r.token_symbol}** | `{r.confidence_tier}` | **{r.meta_score:.1f}** | {r.engine_hits}{star} "
                f"| {res_norm} | ${format_price(r.cb_gecko_price)} | {feat_str} |"
            )
        lines += ["", "---", ""]

    # ── 1. 引擎健康 ──
    if health:
        lines += [
            "## 🏥 引擎健康状态",
            "",
            "| 引擎 | 状态 | 最后更新 |",
            "|------|------|---------|",
        ]
        for h in health:
            lines.append(f"| {h['engine']} | {h['status']} | {h['detail']} |")
        lines += ["", "---", ""]

    # ── 2. 趋势摘要 ──
    if trend and trend.has_prev:
        lines += [
            f"## 📈 趋势摘要（vs {trend.prev_scan_time}）",
            "",
            "| 指标 | 数值 |",
            "|------|------|",
        ]
        if trend.newcomers:
            names = ", ".join(f"{n['symbol']}({n['score']:.1f})" for n in trend.newcomers[:5])
            lines.append(f"| 🆕 新进 | {len(trend.newcomers)} 个: {names} |")
        else:
            lines.append("| 🆕 新进 | 0 |")

        if trend.exits:
            names = ", ".join(f"{e['symbol']}({e['prev_score']:.1f})" for e in trend.exits[:5])
            lines.append(f"| 🔚 退出 | {len(trend.exits)} 个: {names} |")
        else:
            lines.append("| 🔚 退出 | 0 |")

        lines.append(f"| ⬆ 积分上升 | {trend.score_up} 个 |")
        lines.append(f"| ⬇ 积分下降 | {trend.score_down} 个 |")
        lines.append(f"| → 稳定 | {trend.stable} 个 |")

        if trend.jumps:
            for j in trend.jumps[:3]:
                sign = "+" if j["delta"] > 0 else ""
                lines.append(
                    f"| ⚡ 积分跃变 | {j['symbol']}: {j['prev']:.1f}→{j['curr']:.1f} "
                    f"({sign}{j['delta']:.1f}) [{j['cause']}] |"
                )
        lines += ["", "---", ""]

        # ── 3. 信号变更日志 ──
        if trend.engine_changes or trend.newcomers or trend.exits:
            lines += [
                "## 📋 信号变更日志",
                "",
                "| 代币 | 变更 | 详情 |",
                "|------|------|------|",
            ]
            for n in trend.newcomers[:10]:
                lines.append(f"| {n['symbol']} | 🆕 新进 | 综合 {n['score']:.1f}，{n['engines']} 引擎命中 |")
            for e in trend.exits[:10]:
                lines.append(f"| {e['symbol']} | 🔚 退出 | 上轮 {e['prev_score']:.1f}，{e['reason']} |")
            # 按代币分组引擎变更
            from collections import defaultdict
            grouped = defaultdict(list)
            for ec in trend.engine_changes:
                grouped[ec["symbol"]].append(f"{ec['engine']}:{ec['prev'] or '无'}→{ec['curr'] or '无'}")
            for sym, changes in list(grouped.items())[:10]:
                lines.append(f"| {sym} | 🔄 引擎变更 | {', '.join(changes)} |")
            lines += ["", "---", ""]

    # ── 4. 吸筹排行 ──
    if acc_list:
        lines.append(f"## 🎯 吸筹排行 — {len(acc_list)} 个")
        lines.append("")
        # 带置信度与抗跌韧性
        lines.append("| # | 代币 | 置信度 | 综合分 | Δ | 阶段 | master | opus | unified | whale | CB判定 | 现价 | 共振 | 韧性 |")
        lines.append("|---|------|--------|--------|---|------|--------|------|---------|-------|--------|------|------|------|")

        prev_delta = {}
        if trend and trend.has_prev:
            for sc in trend.score_changes:
                prev_delta[sc["symbol"]] = sc["delta"]

        for i, r in enumerate(acc_list[:config.META_TOP_N], 1):
            d = prev_delta.get(r.token_symbol)
            if d is not None:
                delta_str = f"{d:+.1f}"
            elif trend and any(n["symbol"] == r.token_symbol for n in trend.newcomers):
                delta_str = "🆕"
            else:
                delta_str = ""
            star = "★5" if r.engine_hits >= 5 else str(r.engine_hits)
            res_norm = f"{r.resilience_norm:.2f}" if getattr(r, "resilience_norm", None) is not None else "—"
            lines.append(
                f"| {i} | {r.token_symbol} | `{getattr(r, 'confidence_tier', 'L3-Watch')}` | **{r.meta_score:.1f}** | {delta_str} "
                f"| {STAGE_LABEL.get(r.stage, r.stage)} "
                f"| {r.master_signal} | {r.opus_score:.1f} | {r.unified_signal} | {r.whale_level} "
                f"| {r.cb_verdict} | ${format_price(r.cb_gecko_price)} | {star} | {res_norm} |"
            )
        lines.append("")

    # ── 5. 出货预警 ──
    if dist_list:
        lines.append(f"## 💀 出货预警 — {len(dist_list)} 个")
        lines.append("")
        lines.append("| 代币 | 综合分 | 阶段 | unified | CB判定 | 暴利区% | 现价 |")
        lines.append("|------|--------|------|---------|--------|---------|------|")
        for r in dist_list[:config.META_TOP_N]:
            lines.append(
                f"| {r.token_symbol} | **{r.meta_score:.1f}** "
                f"| {STAGE_LABEL.get(r.stage, r.stage)} "
                f"| {r.unified_signal} | {r.cb_verdict} "
                f"| {r.cb_windfall_pct:.1f}% | ${format_price(r.cb_gecko_price)} |"
            )
        lines.append("")

    # ── 6. 积分明细 ──
    if acc_list:
        lines.append("## 📋 积分明细（Top 20）")
        lines.append("")
        lines.append("| 代币 | 综合 | master | opus | unified | whale | CB | 引擎 |")
        lines.append("|------|------|--------|------|---------|-------|----|------|")
        for r in acc_list[:20]:
            lines.append(
                f"| {r.token_symbol} | {r.meta_score:.1f} "
                f"| {r.master_score:.0f} | {r.opus_score:.1f} "
                f"| {r.unified_score:.0f} | {r.whale_score:.0f} "
                f"| {r.cb_score:.0f} | {r.engine_hits} |"
            )
        lines.append("")

    # ── 7. 引擎矛盾 ──
    if conflicts:
        lines.append(f"## ⚠ 引擎矛盾 — {len(conflicts)} 个")
        lines.append("")
        lines.append("| 代币 | 规则 | 综合分 | 详情 |")
        lines.append("|------|------|--------|------|")
        for c in conflicts:
            lines.append(f"| {c.symbol} | {c.rule} | {c.score:.1f} | {c.detail} |")
        lines.append("")

    lines.append("---")
    lines.append(f"*生成时间: {datetime.now().isoformat()}*")
    return "\n".join(lines)
