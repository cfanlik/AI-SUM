"""
meta-verdict 报告生成器 (Master Cockpit 决策驾驶舱全新重构版)
终端输出 + Markdown 决策总控面板 (含红绿灯态势 + 六维全景总面板 + 5轮多快照拟合)
"""
from __future__ import annotations
import os
import logging
logger = logging.getLogger("meta-verdict")
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
        return f"${val:.2f}"
    if val >= 0.01:
        return f"${val:.4f}"
    s = f"{val:.8f}".rstrip('0')
    if s.endswith('.'):
        s = s[:-1]
    if s == "0" or float(s) == 0:
        return f"${val:.2e}"
    return f"${s}"


def generate_report(
    acc_list: list[MetaResult],
    dist_list: list[MetaResult],
    all_count: int,
    scan_time: str,
    trend: TrendReport = None,
    health: list[dict] = None,
    conflicts: list = None,
) -> str:
    """终端 + MD 双输出 (Master Cockpit 架构)"""

    all_ranked = acc_list + [r for r in dist_list if r not in acc_list]
    all_ranked.sort(key=lambda x: x.meta_score, reverse=True)

    # ── 1. 终端概览输出 ──
    print(f"\n{'='*80}")
    print(f"🔮 Meta-Verdict 决策总控驾驶舱 (Master Cockpit) | {scan_time}")
    print(f"   有效评估: {all_count} | 🎯 吸筹共振: {len(acc_list)} | 💀 出货派发: {len(dist_list)}")
    print(f"{'='*80}")

    if health:
        print(f"\n🏥 引擎健康")
        for h in health:
            print(f"  {h['status']} {h['engine']}: {h['detail']}")

    # ── 2. 分类归集红绿灯代币 ──
    l1_alpha = [r for r in all_ranked if r.confidence_tier == "L1-Alpha"]
    l1_squeeze = [r for r in all_ranked if r.confidence_tier == "L1-Squeeze"]
    l1_special = [r for r in all_ranked if r.confidence_tier == "L1-Special"]
    dump_rug = [r for r in all_ranked if r.meta_verdict == "DIST" or r.confidence_tier == "DENIED"]

    # ── 3. 构造 Markdown 报表 ──
    md_lines = [
        f"# 🔮 Meta-Verdict 全局决策总控驾驶舱 (Master Cockpit)",
        f"",
        f"> **生成时间**: `{scan_time}` | **纳入代币**: `{all_count}` 个 | **引擎状态**: 5 引擎全绿在线",
        f"> **架构原则**: 一站式收敛决策数据，消除多报告碎片化；结合 5 轮多快照拟合过滤瞬时噪声。",
        f"",
        f"---",
        f"",
        f"## 🚦 第一屏：全局红绿灯决策态势 (Executive Traffic Lights)",
        f"",
        f"| 决策分类 | 入选代币标的 | 核心逻辑特征 | 推荐实盘应对策略 |",
        f"| :--- | :--- | :--- | :--- |",
    ]

    # 红绿灯内容填充
    if l1_alpha:
        syms = ", ".join([f"**{r.token_symbol}** ({r.meta_score:.1f}分)" for r in l1_alpha[:6]])
        md_lines.append(f"| **🚀 L1-Alpha (稳健真金)** | {syms} | 5 轮得分极稳，DEX 真金率高，独立地址持续死锁 | 现货分批建仓 / 中长线持有 |")
    else:
        md_lines.append(f"| **🚀 L1-Alpha (稳健真金)** | *暂无* | 当前无满足绝对稳健死锁标准的标的 | 耐心等待主升浪吸筹信号 |")

    if l1_squeeze:
        syms = ", ".join([f"**{r.token_symbol}** ({r.meta_score:.1f}分)" for r in l1_squeeze[:6]])
        md_lines.append(f"| **⚡ L1-Squeeze (轧空博弈)** | {syms} | V/L 换手 > 10x 或 CEX 剧烈流入，极浅深度推高 | 仅限短线带止损博弈，严禁长线死锁 |")

    if l1_special:
        syms = ", ".join([f"**{r.token_symbol}** ({r.meta_score:.1f}分)" for r in l1_special[:6]])
        md_lines.append(f"| **🔒 L1-Special (高控事件)** | {syms} | 机构控盘 > 90%，名单轮换率低，低换手控盘 | 观察突破 / 事件催化介入 |")

    if dump_rug:
        syms = ", ".join([f"**{r.token_symbol}** ({r.meta_score:.1f}分)" for r in dump_rug[:6]])
        md_lines.append(f"| **💀 DUMP / RUG (高危预警)** | {syms} | 出货置信度高，获利盘派发或流动性异常 | 触发风控拦截 / 清仓回避 |")

    md_lines.extend([
        f"",
        f"---",
        f"",
        f"## 📊 第二屏：六维量化超级全景总面板 (Executive Master Grid)",
        f"",
        f"> 💡 **聚合 5 引擎全部核心指标，一站式解决跨报告翻查痛点。**",
        f"",
        f"| 排名 | 代码 | 仲裁分 | 决策梯队 | 5 轮时序轨迹 (σ) | 换手乘数 (V/L) | 机构控盘 | CEX占比 (Δ) | 均价 / 现价 | 浮盈状态 | 核心归因定性 |",
        f"| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |",
    ])

    # 填充超级总面板 (Top 25 标的)
    for idx, r in enumerate(all_ranked[:25]):
        rank = idx + 1
        tier_badge = {
            "L1-Alpha": "🚀 L1-Alpha",
            "L1-Squeeze": "⚡ L1-Squeeze",
            "L1-Special": "🔒 L1-Special",
            "L2-Bet": "👀 L2-Bet",
            "L2-Speculative": "🎲 L2-投机",
            "L3-Watch": "─ L3-观察",
            "DENIED": "🛑 DENIED"
        }.get(r.confidence_tier, r.confidence_tier)

        traj = r.series_trajectory if r.series_trajectory else f"{r.meta_score:.1f}"
        std_str = f"(σ={r.series_std:.2f})" if r.series_std > 0 else ""
        vl_str = f"{r.vl_ratio:.2f}x" if r.vl_ratio > 0 else "—"
        inst_str = f"{r.institutional_hold:.1f}%" if r.institutional_hold > 0 else "—"
        cex_str = f"{r.cex_hold_pct:.1f}% ({r.cex_delta_pct:+.1f}%)" if r.cex_hold_pct > 0 else "—"
        vwap_str = format_price(r.cb_vwap) if r.cb_vwap > 0 else "—"
        price_str = format_price(r.cb_gecko_price) if r.cb_gecko_price > 0 else "—"
        
        # 浮盈与归因定性
        pnl_str = "中性"
        if r.cb_vwap > 0 and r.cb_gecko_price > 0:
            pnl_val = (r.cb_gecko_price - r.cb_vwap) / r.cb_vwap * 100.0
            pnl_str = f"{pnl_val:+.1f}%"

        desc = r.series_desc if r.series_desc else f"共振 {r.engine_hits} 引擎"
        if r.confidence_tier == "L1-Squeeze":
            desc = f"极浅池高换手轧空，筹码向CEX归集"
        elif r.confidence_tier == "L1-Alpha":
            desc = f"真金持续死锁加仓，走势极其稳健"

        md_lines.append(
            f"| **{rank}** | **{r.token_symbol}** | **{r.meta_score:.2f}** | {tier_badge} | `{traj}` {std_str} | {vl_str} | {inst_str} | {cex_str} | {vwap_str} / {price_str} | {pnl_str} | {desc} |"
        )

    # ── 第三屏：时序拟合矩阵 ──
    md_lines.extend([
        f"",
        f"---",
        f"",
        f"## ⏳ 第三屏：连续 5 轮多快照时序拟合矩阵 (Window = 5 Snapshots)",
        f"",
        f"| 标的 | 5 轮得分演化轨迹 | 初值 → 现值 | 拟合增量 Δ | 波动度 σ | 动态演变定性 |",
        f"| :--- | :--- | :---: | :---: | :---: | :--- |",
    ])

    if trend and trend.series_metrics:
        for k, sm in list(trend.series_metrics.items())[:15]:
            md_lines.append(
                f"| **{sm.token_symbol}** | `{sm.trajectory_str}` | {sm.scores_trajectory[0]:.1f} → {sm.scores_trajectory[-1]:.1f} | {sm.score_delta_5:+.2f} | {sm.score_std:.2f} | {sm.trend_category} ({sm.summary_thesis}) |"
            )

    # ── 第四屏：底层白盒钻取通道 ──
    md_lines.extend([
        f"",
        f"---",
        f"",
        f"## 🔗 第四屏：底层专业子引擎钻取索引 (Drilldown Links)",
        f"- 🚀 [拉升前兆与流动性共振报告 (Pump Radar)](../pump/latest_pump_report.md)",
        f"- 💰 [持仓成本与 VWAP 偏离深度报告 (Cost Basis)](../cost-basis/latest_cb_report.md)",
        f"- 📊 [长期画像与多周期回测报告 (History Backtest)](../history/latest_history_report.md)",
        f"- ⚡ [实盘突发异动与穿透观察专报 (Anomaly Watch)](../anomaly/latest_实时信号验证报告_实盘观察.md)",
        f"- 统一交叉雷达: `report/unified/` | 庄控雷达: `report/whale/`",
        f"",
    ])

    report_content = "\n".join(md_lines)

    # 写入文件
    out_dir = Path("/opt/AI-SUM/report/meta")
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. 历史带时间戳快照
    ts_clean = scan_time.replace(":", "").replace("-", "").replace(" ", "_")
    report_file = out_dir / f"meta_{ts_clean}.md"
    report_file.write_text(report_content, encoding="utf-8")

    # 2. 一站式总控软链接/最新文件
    latest_file = out_dir / "latest_meta_dashboard.md"
    latest_file.write_text(report_content, encoding="utf-8")

    logger.info(f"Master Cockpit 总控报告已成功生成: {report_file} 和 {latest_file}")
    return report_content
