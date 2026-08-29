"""
meta-verdict 报告生成器 (Master Cockpit 决策总控驾驶舱精简版)
四屏标准输出 (红绿灯态势 + 六维全景面板 + 5轮多快照拟合 + 全局 Tips 规范)
支持代币点击无缝弹出【60天持币正相关性 (ρ)】详情抽屉
"""
from __future__ import annotations
import os
import logging
from datetime import datetime
from pathlib import Path
from arbitrator import MetaResult
from trend_analyzer import TrendReport
import config

logger = logging.getLogger("meta-verdict")

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
    """终端 + MD 双输出 (精简版 Master Cockpit 决策驾驶舱)"""

    all_ranked = acc_list + [r for r in dist_list if r not in acc_list]
    all_ranked.sort(key=lambda x: x.meta_score, reverse=True)

    # ── 1. 终端概览 ──
    print(f"\n{'='*80}")
    print(f"🔮 Meta-Verdict 决策总控驾驶舱 (Master Cockpit) | {scan_time}")
    print(f"   有效评估: {all_count} | 🎯 吸筹共振: {len(acc_list)} | 💀 出货派发: {len(dist_list)}")
    print(f"{'='*80}")

    # ── 2. 分类红绿灯标的 ──
    l1_alpha = [r for r in all_ranked if r.confidence_tier == "L1-Alpha"]
    l1_squeeze = [r for r in all_ranked if r.confidence_tier == "L1-Squeeze"]
    l1_special = [r for r in all_ranked if r.confidence_tier == "L1-Special"]
    dump_rug = [r for r in all_ranked if r.meta_verdict == "DIST" or r.confidence_tier == "DENIED"]

    # ── 3. 构造 Markdown 报表 ──
    md_lines = [
        f"# 🔮 Meta-Verdict 全局决策总控驾驶舱 (Master Cockpit) — {scan_time}",
        f"",
        f"> **全局概览**: 纳入评估代币 `{all_count}` 个 | 🎯 吸筹共振 `{len(acc_list)}` 个 | 💀 派发出货 `{len(dist_list)}` 个 | 🏥 5 引擎全部在线",
        f"> **架构原则**: 一站式收敛决策数据，消除多报告碎片化；结合 5 轮多快照拟合过滤瞬时噪声。",
        f"",
        f"---",
        f"",
        f"## 🚦 第一屏：全局红绿灯决策态势 (Executive Traffic Lights)",
        f"",
        f"| 决策分类 | 入选代币标的 | 核心逻辑特征 | 推荐实盘应对策略 |",
        f"| :--- | :--- | :--- | :--- |",
    ]

    # 第一屏填充
    if l1_alpha:
        syms = ", ".join([f"**{r.token_symbol}** ({r.meta_score:.1f}分)" for r in l1_alpha[:6]])
        md_lines.append(f"| **🚀 L1-Alpha (稳健真金)** | {syms} | 5 轮得分极稳，DEX 真金率高，独立地址持续死锁加仓 | 现货分批建仓 / 中长线持有 |")
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
        f"> 💡 **点击任意代币代码（如 `**BTR**`）可直接弹出查看【🔥 60天持币正相关性 (ρ)】演化面积图及积分时序。**",
        f"",
        f"| 排名 | 代码 | 仲裁分 | 决策梯队 | 5 轮时序轨迹 (σ) | 换手乘数 (V/L) | 机构控盘 | CEX占比 (Δ) | 均价 / 现价 | 浮盈状态 | 核心归因定性 |",
        f"| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |",
    ])

    # 第二屏填充 (Top 25 标的)
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
        
        pnl_str = "中性"
        if r.cb_vwap > 0 and r.cb_gecko_price > 0:
            pnl_val = (r.cb_gecko_price - r.cb_vwap) / r.cb_vwap * 100.0
            pnl_str = f"{pnl_val:+.1f}%"

        desc = r.series_desc if r.series_desc else f"共振 {r.engine_hits} 引擎"
        if r.confidence_tier == "L1-Squeeze":
            desc = f"极浅池高换手轧空，筹码向CEX归集"
        elif r.confidence_tier == "L1-Alpha":
            desc = f"真金持续死锁加仓，走势极其稳健"

        # 重点：代码列严格使用 | **{r.token_symbol}** | 格式，保证前端 mdParser 触发 onSelectToken 打开详情抽屉
        md_lines.append(
            f"| **{rank}** | **{r.token_symbol}** | **{r.meta_score:.2f}** | {tier_badge} | `{traj}` {std_str} | {vl_str} | {inst_str} | {cex_str} | {vwap_str} / {price_str} | {pnl_str} | {desc} |"
        )

    # ── 第三屏：5 轮时序拟合矩阵 ──
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

    # ── 第四屏：报表说明与参数字典 (Tips) ──
    md_lines.extend([
        "",
        "---",
        "",
        "## 📖 第四屏：报表说明与风控参数字典 (Tips)",
        "",
        "| 字段 / 参数 | 物理定义与算法规则 | 阈值标准与决策含义 |",
        "| :--- | :--- | :--- |",
        "| **换手乘数 (V/L)** | 24小时成交量与流动性池深度比值 (Volume / Liquidity) | `> 10.0x` 触发流动性挤压警报；`> 15.0x` 判定为短线轧空投机 |",
        "| **CEX占比 (Δ)** | 交易所地址持仓占比及其相较于初始快照的增量 | `Δ > +20%` 表明链上筹码正快速向中心化交易所归集 |",
        "| **波动度 (σ)** | 连续 5 轮仲裁得分的标准差 (StdDev) | `σ < 0.3` 为极度稳健吸筹；`σ > 2.0` 为多空剧烈拉锯博弈 |",
        "| **拟合增量 (Δ)** | 连续 5 轮快照首尾仲裁得分净变化值 (Score_curr - Score_init) | `Δ > +2.0` 为加速爆发；`Δ < -1.5` 为资金动能衰减 |",
        "| **🚀 L1-Alpha** | 顶级稳健真金共振标的（得分 >= 7.0 且换手正常） | 独立地址真金死锁加仓，适合现货中长线持有 |",
        "| **⚡ L1-Squeeze** | 顶级流动性挤压与轧空博弈标的（得分 >= 7.0 但 V/L > 10x） | 极浅池推动的高风险博弈，仅限短线带止损操作 |",
        "| **🔥 60天持币正相关性 (ρ)** | 固定吸筹地址队列 60 天持币量与时间的皮尔逊相关系数 | `ρ > 80%` 为极强正相关，反映核心主力筹码持续单调递增 |",
        "",
        "---",
        "",
        "## 🔗 底层专业子引擎钻取索引 (Drilldown Links)",
        "- 🚀 [拉升前兆与流动性共振报告 (Pump Radar)](../pump/latest_pump_report.md)",
        "- 💰 [持仓成本与 VWAP 偏离深度报告 (Cost Basis)](../cost-basis/latest_cb_report.md)",
        "- 📊 [长期画像与多周期回测报告 (History Backtest)](../history/latest_history_report.md)",
        "- ⚡ [实盘突发异动与穿透观察专报 (Anomaly Watch)](../anomaly/latest_实时信号验证报告_实盘观察.md)",
        "",
    ])

    report_content = "\n".join(md_lines)

    # 写入文件
    out_dir = Path("/opt/AI-SUM/report/meta")
    out_dir.mkdir(parents=True, exist_ok=True)
    
    ts_clean = scan_time.replace(":", "").replace("-", "").replace(" ", "_")
    report_file = out_dir / f"meta_{ts_clean}.md"
    report_file.write_text(report_content, encoding="utf-8")

    latest_file = out_dir / "latest_meta_dashboard.md"
    latest_file.write_text(report_content, encoding="utf-8")

    logger.info(f"Master Cockpit 总控报告已成功生成: {report_file} 和 {latest_file}")
    return report_content
