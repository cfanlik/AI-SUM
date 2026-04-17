"""
whale-scan — 报告生成器
终端输出 + MD 雷达报 + 单币诊断
"""
from __future__ import annotations
import os
from datetime import datetime
import config
from whale_verdict import WhaleVerdict


# ════════════════════════════════════════
# 终端输出
# ════════════════════════════════════════

def print_radar(results: list[WhaleVerdict], elapsed: float):
    """终端打印全库雷达"""
    high = [r for r in results if r.level == "HIGH"]
    med = [r for r in results if r.level == "MEDIUM"]
    low = [r for r in results if r.level == "LOW"]
    clean = [r for r in results if r.level == "CLEAN"]

    print(f"\n{'='*90}")
    print(f" 🐋 庄控预警雷达 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f" 扫描 {len(results)} 代币 | 🔴高危 {len(high)} | 🟠中危 {len(med)} | 🟡低危 {len(low)} | ✅安全 {len(clean)}")
    print(f" 耗时 {elapsed:.1f}s")
    print(f"{'='*90}")

    if high:
        print(f"\n🔴 高危庄控 (≥{config.LEVEL_HIGH:.0f}%)")
        _print_table(high)
    if med:
        print(f"\n🟠 中危观察 (≥{config.LEVEL_MEDIUM:.0f}%)")
        _print_table(med[:config.TOP_N_REPORT])
    if low:
        print(f"\n🟡 低危记录 (≥{config.LEVEL_LOW:.0f}%) — 前10")
        _print_table(low[:10])


def _print_table(items: list[WhaleVerdict]):
    fmt = "{:>4} {:<10} {:>6} {:>7} {:>7} {:>7} {:>7} {:>6} {:>8} {:>8} {:>4}"
    print(fmt.format("Rank", "Symbol", "Conf%", "Top2%", "Top5%", "Top10%", "T2DEX", "W1Δ", "Prc24h", "M/L", "C95"))
    print("-" * 90)
    for i, v in enumerate(items, 1):
        cp = v.concentration
        dr = v.drift
        t2d = f"{cp.top2_avg_dex:.2f}" if cp and cp.top2_avg_dex >= 0 else "N/A"
        w1d = f"+{dr.whale1_hold_delta:.1f}" if dr else "?"
        pchg = f"+{v.price_change_24h:.0f}%" if v.price_change_24h > 0 else f"{v.price_change_24h:.0f}%"
        ml = f"{v.mcap_liq_ratio:.0f}x" if v.mcap_liq_ratio > 0 else "N/A"
        lp = f"${v.lp_usd:,.0f}" if v.lp_usd > 0 else "N/A"
        vl = f"{v.vl_ratio:.2f}" if v.vl_ratio > 0 else "0"
        c95 = cp.cov95_count if cp else 0
        print(fmt.format(
            i,
            v.symbol[:10],
            f"{v.confidence:.1f}%",
            f"{cp.top2_hold:.1f}%" if cp else "?",
            f"{cp.top5_hold:.1f}%" if cp else "?",
            f"{cp.top10_hold:.1f}%" if cp else "?",
            t2d,
            w1d,
            pchg,
            ml,
            c95,
        ))


def print_single(v: WhaleVerdict):
    """终端打印单币诊断"""
    print(f"\n{'='*70}")
    print(f" 🐋 {v.symbol} ({v.chain}) — 庄控诊断")
    print(f" 置信度: {v.confidence:.1f}% [{v.level}]")
    print(f" 评分: {v.actual_score}/{v.max_score}")
    print(f"{'='*70}")

    print(f"\n证据链:")
    fmt = "  {:>2} {:<4} {:<24} {:>3} {:>4}  {}"
    print(fmt.format("", "维度", "信号", "权重", "状态", "详情"))
    print("  " + "-" * 66)
    for s in v.signals:
        status = "✅" if s["hit"] else "❌"
        print(fmt.format("", s["dim"], s["name"], s["weight"], status, s["detail"]))

    cp = v.concentration
    if cp and cp.cov95_addresses:
        print(f"\n监控地址 (覆盖95%, {cp.cov95_count}个):")
        fmt2 = "  {:>4} {:>44} {:>8} {:>8} {:>10} {:>10} {}"
        print(fmt2.format("Rank", "Address", "Hold%", "Cum%", "Type", "48h_Out", "Label"))
        print("  " + "-" * 100)
        for a in cp.cov95_addresses:
            print(fmt2.format(
                a["rank"], a["address"],
                f"{a['hold_pct']:.4f}%", f"{a['cumulative']:.4f}%",
                a["type"], f"{a['h48_out']:,.0f}", a["label"][:25],
            ))


# ════════════════════════════════════════
# MD 报告
# ════════════════════════════════════════

def save_md_radar(results: list[WhaleVerdict]) -> str:
    """保存全库雷达 MD 报告"""
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M")
    path = os.path.join(config.REPORT_DIR, f"whale_{ts}.md")

    high = [r for r in results if r.level == "HIGH"]
    med = [r for r in results if r.level == "MEDIUM"]

    lines = [
        f"# 🐋 庄控预警雷达 — {now.strftime('%Y-%m-%d %H:%M')}",
        "",
        f"> whale-scan V1.0 | 扫描 {len(results)} 代币 | "
        f"🔴高危 {len(high)} | 🟠中危 {len(med)}",
        "",
        "---",
        "",
    ]

    if high:
        lines.append(f"## 🔴 高危庄控 (≥{config.LEVEL_HIGH:.0f}%)")
        lines.append("")
        lines.append("| 代币 | 链 | 置信度 | Top2% | Top10% | DEX率 | W1漂移 | 价格24h | M/L比 | LP($) | V/L | C95 | 状态 |")
        lines.append("|------|----|---------:|------:|-------:|------:|------:|-------:|------:|--------:|-----:|----:|------|")
        for v in high:
            cp = v.concentration
            dr = v.drift
            t2d = f"{cp.top2_avg_dex:.0%}" if cp and cp.top2_avg_dex >= 0 else "N/A"
            w1d = f"+{dr.whale1_hold_delta:.1f}%" if dr else "?"
            status = "🔴派发中" if any(s["hit"] and s["name"] == "whale1_distributing" for s in v.signals) else "⚠️监控"
            lines.append(
                f"| {v.symbol} | {v.chain} | {v.confidence:.1f}% | "
                f"{cp.top2_hold:.1f}% | {cp.top10_hold:.1f}% | {t2d} | {w1d} | "
                f"+{v.price_change_24h:.0f}% | {v.mcap_liq_ratio:.0f}x | "
                f"${v.lp_usd:,.0f} | {v.vl_ratio:.2f} | {cp.cov95_count} | {status} |"
            )
        lines.append("")

        # 每个高危代币附带监控地址表
        for v in high:
            lines.extend(_md_single_detail(v))

    if med:
        lines.append(f"\n## 🟠 中危观察 (≥{config.LEVEL_MEDIUM:.0f}%)")
        lines.append("")
        lines.append("| 代币 | 链 | 置信度 | Top2% | Top10% | W1漂移 | M/L比 | 信号数 |")
        lines.append("|------|----|---------:|------:|-------:|------:|------:|------:|")
        for v in med[:config.TOP_N_REPORT]:
            cp = v.concentration
            dr = v.drift
            triggered = sum(1 for s in v.signals if s["hit"])
            lines.append(
                f"| {v.symbol} | {v.chain} | {v.confidence:.1f}% | "
                f"{cp.top2_hold:.1f}% | {cp.top10_hold:.1f}% | "
                f"+{dr.whale1_hold_delta:.1f}% | {v.mcap_liq_ratio:.0f}x | "
                f"{triggered}/{len(v.signals)} |"
            )
        lines.append("")

    lines.append("---")
    lines.append(f"_whale-scan V1.0 | {now.strftime('%Y-%m-%d %H:%M')}_")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def save_md_single(v: WhaleVerdict) -> str:
    """保存单币诊断 MD 报告"""
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M")
    path = os.path.join(config.REPORT_DIR, f"{v.symbol}_{ts}.md")

    lines = [
        f"# {v.symbol} ({v.chain}) — 庄控深度诊断",
        "",
        f"## 裁决: {'庄家控盘' if v.level == 'HIGH' else '观察中' if v.level in ('MEDIUM', 'LOW') else '正常'}",
        f"- 庄控置信度: **{v.confidence:.1f}%**",
        f"- 评分: {v.actual_score}/{v.max_score}",
        f"- 快照数: {v.snap_count}",
        "",
    ]

    lines.extend(_md_single_detail(v))

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def _md_single_detail(v: WhaleVerdict) -> list[str]:
    """生成单币证据链+监控地址的 MD 行"""
    lines = []
    cp = v.concentration
    dr = v.drift

    # 证据链
    lines.append(f"### {v.symbol} 证据链")
    lines.append("")
    lines.append("| 状态 | 维度 | 信号 | 权重 | 详情 |")
    lines.append("|------|------|------|------|------|")
    for s in v.signals:
        status = "✅" if s["hit"] else "❌"
        lines.append(f"| {status} | {s['dim']} | {s['name']} | {s['weight']} | {s['detail']} |")
    lines.append("")

    # Top20 概览
    if cp and cp.top20_details:
        lines.append(f"### {v.symbol} Top20 持仓概览")
        lines.append("")
        lines.append("| # | 地址 | 持仓% | 买入($) | 卖出($) | DEX率 | 类型 | 48h出 | 标签 |")
        lines.append("|---:|:---|---:|---:|---:|---:|:---|---:|:---|")
        for d in cp.top20_details:
            dr_val = f"{d['dex_ratio']:.2f}" if d['dex_ratio'] is not None else "N/A"
            typ = "CEX" if d["is_cex"] else ("CONTRACT" if d["is_contract"] else ("DEX" if d["is_dex"] else "WALLET"))
            lines.append(
                f"| {d['rank']} | `{d['address'][:16]}...` | {d['hold_pct']:.4f}% | "
                f"${d['buy_usd']:,.0f} | ${d['sell_usd']:,.0f} | {dr_val} | "
                f"{typ} | {d['h48_out']:,.0f} | {d['label'][:20]} |"
            )
        lines.append("")

    # 监控地址表 (95%覆盖)
    if cp and cp.cov95_addresses:
        lines.append(f"### {v.symbol} 监控地址 (覆盖95%, {cp.cov95_count}个)")
        lines.append("")
        lines.append("| # | 地址 | 持仓% | 累计% | 类型 | 48h出 | 标签 |")
        lines.append("|---:|:---|---:|---:|:---|---:|:---|")
        for a in cp.cov95_addresses:
            lines.append(
                f"| {a['rank']} | `{a['address']}` | {a['hold_pct']:.4f}% | "
                f"{a['cumulative']:.4f}% | {a['type']} | {a['h48_out']:,.0f} | {a['label'][:25]} |"
            )
        lines.append("")

    return lines
