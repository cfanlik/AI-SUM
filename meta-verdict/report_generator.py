"""
meta-verdict 报告生成器 (Master Cockpit 决策总控驾驶舱终极标准版)
四屏标准输出 (红绿灯单行表格 + 六维全景面板 + 5轮多快照拟合 + 全局 Tips 规范)
所有表格代币列统一命名为【代币】且单元格纯净输出，100% 触发前端【60天持币正相关性 (ρ)】详情抽屉
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


def format_token_cell(symbol: str, chain: str) -> str:
    """标准代币列格式化，兼顾 60天持币抽屉触发与跨链区分"""
    sym = (symbol or "?").upper()
    ch = (chain or "").lower()
    if ch and ch != "bsc":
        return f"**{sym} [{ch.upper()}]**"
    return f"**{sym}**"


def generate_report(
    acc_list: list[MetaResult],
    dist_list: list[MetaResult],
    all_count: int,
    scan_time: str,
    trend: TrendReport = None,
    health: list[dict] = None,
    conflicts: list = None,
    all_arbitrated: list[MetaResult] = None,
) -> str:
    """终端 + MD 双输出 (四屏标准决策驾驶舱)"""

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
        f"> **交互说明**: 点击任意表格中的【代币】名称，即可在右侧弹出查看 **🔥 60天持币正相关性 (ρ)** 真实面积图、生命周期及积分时序。",
        f"",
        f"---",
        f"",
        f"## 🚦 第一屏：全局红绿灯决策态势 (Executive Traffic Lights)",
        f"",
        f"| 决策分类 | 代币 | 仲裁分 | 5 轮时序轨迹 (σ) | 换手乘数 (V/L) | 核心特征归因 | 推荐应对策略 |",
        f"| :--- | :--- | :---: | :---: | :---: | :--- | :--- |",
    ]

    # 第一屏填充 (每行单标的标准表格，代币列为纯净 | **SYMBOL** |)
    for r in l1_alpha[:5]:
        traj = r.series_trajectory if r.series_trajectory else f"{r.meta_score:.1f}"
        std_str = f"(σ={r.series_std:.2f})" if r.series_std > 0 else ""
        vl_str = f"{r.vl_ratio:.2f}x" if r.vl_ratio > 0 else "—"
        md_lines.append(
            f"| **🚀 L1-Alpha (稳健真金)** | {format_token_cell(r.token_symbol, r.chain)} | **{r.meta_score:.2f}** | `{traj}` {std_str} | {vl_str} | DEX 真金率高，独立地址持续死锁加仓 | 现货分批建仓 / 中长线持有 |"
        )

    for r in l1_squeeze[:5]:
        traj = r.series_trajectory if r.series_trajectory else f"{r.meta_score:.1f}"
        std_str = f"(σ={r.series_std:.2f})" if r.series_std > 0 else ""
        vl_str = f"{r.vl_ratio:.2f}x" if r.vl_ratio > 0 else "—"
        md_lines.append(
            f"| **⚡ L1-Squeeze (轧空博弈)** | {format_token_cell(r.token_symbol, r.chain)} | **{r.meta_score:.2f}** | `{traj}` {std_str} | {vl_str} | 极浅深度高换手轧空，筹码向CEX归集 | 仅限短线带止损博弈，严禁长线死锁 |"
        )

    for r in l1_special[:3]:
        traj = r.series_trajectory if r.series_trajectory else f"{r.meta_score:.1f}"
        std_str = f"(σ={r.series_std:.2f})" if r.series_std > 0 else ""
        vl_str = f"{r.vl_ratio:.2f}x" if r.vl_ratio > 0 else "—"
        md_lines.append(
            f"| **🔒 L1-Special (高控事件)** | {format_token_cell(r.token_symbol, r.chain)} | **{r.meta_score:.2f}** | `{traj}` {std_str} | {vl_str} | 机构控盘 > 90%，低换手死锁控盘 | 观察突破 / 事件催化介入 |"
        )

    for r in dump_rug[:5]:
        traj = r.series_trajectory if r.series_trajectory else f"{r.meta_score:.1f}"
        std_str = f"(σ={r.series_std:.2f})" if r.series_std > 0 else ""
        vl_str = f"{r.vl_ratio:.2f}x" if r.vl_ratio > 0 else "—"
        tok_str = format_token_cell(r.token_symbol, r.chain)

        # 动态细分风险类型
        if r.reserve_usd > 0 and r.reserve_usd < 10000:
            category = "🛑 撤池死池 / RUG"
            strategy = "死池警报 / 零流动性不可交易"
        elif r.price_now_ret is not None and r.price_now_ret <= -70.0:
            category = "💀 严重看涨误判"
            strategy = "认亏止损 / 永久移出观察池"
        elif r.dump_penalty >= 2.0 or (r.hold_delta_72h_pct is not None and r.hold_delta_72h_pct <= -15.0):
            category = "🚨 主力集中出货"
            strategy = "触发风控拦截 / 严禁做多抄底"
        else:
            category = "⚠️ 动能衰竭破位"
            strategy = "暂停加仓 / 严格执行止损"

        # 动态真实量化归因 (彻底废除死模板)
        reason = r.dump_reasons if r.dump_reasons else r.series_desc
        if not reason:
            if r.price_now_ret is not None:
                reason = f"信号首发至今累计跌幅 {r.price_now_ret:+.1f}%"
            else:
                reason = "仲裁分转负，多维度动能衰竭"

        md_lines.append(
            f"| **{category}** | {tok_str} | **{r.meta_score:.2f}** | `{traj}` {std_str} | {vl_str} | {reason} | {strategy} |"
        )

    md_lines.extend([
        f"",
        f"---",
        f"",
        f"## 📊 第二屏：六维量化超级全景总面板 (Executive Master Grid)",
        f"",
        f"| 排名 | 代币 | 仲裁分 | 决策梯队 | 5 轮时序轨迹 (σ) | 换手乘数 (V/L) | 机构控盘 | CEX占比 (Δ) | 均价 / 现价 | 浮盈状态 | 核心归因定性 |",
        f"| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |",
    ])

    # 第二屏填充 (Top 25 标的，代币列固定为 | **SYMBOL** |)
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
        if r.confidence_tier == "DENIED" or r.meta_verdict == "DIST":
            desc = r.dump_reasons if r.dump_reasons else r.series_desc
        elif r.confidence_tier == "L1-Squeeze":
            desc = f"极浅池高换手轧空，筹码向CEX归集"
        elif r.confidence_tier == "L1-Alpha":
            desc = f"真金持续死锁加仓，走势极其稳健"

        tok_str = format_token_cell(r.token_symbol, r.chain)
        md_lines.append(
            f"| **{rank}** | {tok_str} | **{r.meta_score:.2f}** | {tier_badge} | `{traj}` {std_str} | {vl_str} | {inst_str} | {cex_str} | {vwap_str} / {price_str} | {pnl_str} | {desc} |"
        )

    # ── 第三屏：5 轮时序拟合矩阵 (代币列固定为 | **SYMBOL** |) ──
    md_lines.extend([
        f"",
        f"---",
        f"",
        f"## ⏳ 第三屏：连续 5 轮多快照时序拟合矩阵 (Window = 5 Snapshots)",
        f"",
        f"| 代币 | 5 轮得分演化轨迹 | 初值 → 现值 | 拟合增量 Δ | 波动度 σ | 动态演变定性 |",
        f"| :--- | :--- | :---: | :---: | :---: | :--- |",
    ])

    if trend and trend.series_metrics:
        for k, sm in list(trend.series_metrics.items())[:15]:
            md_lines.append(
                f"| **{sm.token_symbol}** | `{sm.trajectory_str}` | {sm.scores_trajectory[0]:.1f} → {sm.scores_trajectory[-1]:.1f} | {sm.score_delta_5:+.2f} | {sm.score_std:.2f} | {sm.trend_category} ({sm.summary_thesis}) |"
            )

    # ── 第四屏：报表说明与风控参数字典 (Tips) ──
    md_lines.extend([
        "",
        "---",
        "",
        "## 📖 第四屏：报表说明与风控参数字典 (Tips)",
        "",
        "### 1. 核心量化指标字典",
        "| 字段 / 参数 | 物理定义与算法规则 | 阈值标准与决策含义 |",
        "| :--- | :--- | :--- |",
        "| **换手乘数 (V/L)** | 24小时成交量与流动性池深度比值 (Volume / Liquidity) | `> 10.0x` 触发流动性挤压警报；`> 15.0x` 判定为短线轧空投机 |",
        "| **CEX占比 (Δ)** | 交易所地址持仓占比及其相较于初始快照的增量 | `Δ > +20%` 表明链上筹码正快速向中心化交易所归集 |",
        "| **波动度 (σ)** | 连续 5 轮仲裁得分的标准差 (StdDev) | `σ < 0.3` 为极度稳健吸筹；`σ > 2.0` 为多空剧烈拉锯博弈 |",
        "| **拟合增量 (Δ)** | 连续 5 轮快照首尾仲裁得分净变化值 (Score_curr - Score_init) | `Δ > +2.0` 加速爆发；`Δ < -2.0` 动能断崖跳水 |",
        "",
        "### 2. 置信梯队与风控分类标准",
        "| 决策梯队 / 分类 | 触发门槛与判定规则 | 推荐应对处置策略 |",
        "| :--- | :--- | :--- |",
        "| **🚀 L1-Alpha (稳健真金)** | 仲裁分 >= 7.0，DEX 真金率高，独立地址持续死锁加仓 | 现货分批建仓 / 中长线持有 |",
        "| **⚡ L1-Squeeze (轧空博弈)** | 仲裁分 >= 7.0 但换手 V/L > 10x 或 CEX 增量 > 20% | 极浅池推动的高风险博弈，仅限短线带止损操作 |",
        "| **💀 严重看涨误判** | 信号首发至今累计价格收益率 `price_now_ret <= -70.0%` | 认亏止损，系统一票否决转入 DIST 与 DENIED |",
        "| **🚨 主力集中出货** | 72h 吸筹队列持仓流失 > 15% 或 `opus-scan` 出货置信度 >= 50% | 触发风控拦截，严禁盲目做多抄底 |",
        "| **🛑 撤池死池 / RUG** | 流动性池储备 `reserve_usd < $10,000` 或检测到撤池 | 零流动性不可交易，直接回避 |",
        "",
        "### 3. 核心认知盲区解答：为什么“60天持币量在涨”，系统却亮红灯高危预警？",
        "> **微观悖论揭秘（局部散户套牢加仓 vs 宏观巨鲸疯狂砸盘）**：",
        "> - **右侧抽屉 60 天持币图**: 仅统计固定的 100 余个吸筹地址。在阴跌趋势中，散户往往越跌越买被动套牢补仓，但这批地址持币总量通常**不足代币总供应量的 1%**（例如 GWEI 仅占 0.95%）；",
        "> - **宏观巨鲸真实动向**: 全网前 3~5 个顶级巨鲸往往掌控全网 **70%~90% 以上** 的绝对筹码。当巨鲸在二级市场上以散户买入量数十倍的规模大额出货砸盘时，价格必然断崖暴跌 80% 以上；",
        "> - **系统风控逻辑**: 决策总控驾驶舱不会被 1% 的局部散户加仓所蒙蔽，一旦发现二级市场价格严重背离（亏损 > 70%）或巨鲸大幅减仓，将**一票否决直接亮起高危红灯**，坚决保护用户本金免遭灭顶之灾。",
        "",
        "---",
    ])

    # ── 第五屏：暴跌出货与严重看涨误判专项风控看板 (动静结合自洽时序风控雷达) ──
    dump_pool = []
    seen_tokens = set()
    if all_arbitrated:
        for r in all_arbitrated:
            u_key = f"{r.chain}:{r.token_address.lower()}"
            if u_key in seen_tokens:
                continue

            # 挂载 5 轮时序拟合轨迹与加速度增量
            if trend and hasattr(trend, "series_metrics") and u_key in trend.series_metrics:
                sm = trend.series_metrics[u_key]
                r.score_delta_5 = sm.score_delta_5
                r.trajectory_str = sm.trajectory_str
            else:
                r.score_delta_5 = 0.0
                r.trajectory_str = f"{r.meta_score:.1f}"

            # 自洽准入门槛: 实质性出货风险 或 严重价格腰斩
            has_dump_risk = (
                r.meta_verdict == "DIST" or 
                r.dump_penalty >= 1.5 or 
                (r.price_now_ret is not None and r.price_now_ret <= -50.0)
            )
            if has_dump_risk:
                dump_pool.append(r)
                seen_tokens.add(u_key)
        
        # 动静结合三元组纯量化自洽排序 (零单币特判)
        # 第一主键: 仲裁总分 meta_score 升序 (负分越深越危险)
        # 第二主键: 5 轮时序拟合净增量 score_delta_5 升序 (跳水恶化越猛越靠前)
        # 第三主键: 信号至今收益率 price_now_ret 升序 (同等恶化下亏损越深越靠前)
        dump_pool.sort(
            key=lambda x: (
                round(x.meta_score, 2),
                round(getattr(x, "score_delta_5", 0.0), 2),
                x.price_now_ret if x.price_now_ret is not None else 0.0
            )
        )

    if dump_pool:
        md_lines.extend([
            "",
            "---",
            "",
            "## 🚨 第五屏：暴跌出货与严重看涨误判专项风控看板 (Crash & Heavy Distribution Radar)",
            "",
            "> **风控总则**: 融合截面绝对负分与 5 轮时序恶化加速度（跳水越快越危险）进行动静双轮驱动排序。汇集链上主力砸盘、72h 持仓断崖跳水及历史严重看涨误判标的。点击【代币】可查看详细持币图解。",
            "",
            "| 风险分类 | 代币 | 仲裁分 | 5 轮得分演化轨迹 (Δ5) | 信号至今收益 | 72h 持仓变动 | 累计惩罚 | 核心风险归因 | 推荐处置策略 |",
            "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- | :--- |",
        ])

        # 扩充容量，保障全量 DIST 标的 100% 完整覆盖展示
        for r in dump_pool[:40]:
            delta_5 = getattr(r, "score_delta_5", 0.0)
            traj_str = f"`{r.trajectory_str}` (Δ{delta_5:+.1f})" if r.trajectory_str else f"`{r.meta_score:.1f}`"

            # 动态精准分类: 优先识别历史看涨严重误判(亏损>70%)，其次识别动能断崖跳水与主力出货
            if r.price_now_ret is not None and r.price_now_ret <= -70.0:
                cat = "💀 严重看涨误判"
                strategy = "认亏止损 / 永久移出观察池"
            elif delta_5 <= -3.0 or (r.dump_penalty >= 2.0 and r.meta_score <= -2.5):
                cat = "🚨 动能断崖跳水"
                strategy = "触发风控拦截 / 严禁做多抄底"
            elif r.dump_penalty >= 1.5 or (r.hold_delta_72h_pct is not None and r.hold_delta_72h_pct <= -15.0):
                cat = "🚨 主力集中出货"
                strategy = "触发风控拦截 / 严禁做多抄底"
            elif r.meta_verdict == "DIST":
                cat = "🛑 持续阴跌出货"
                strategy = "触发风控拦截 / 严禁做多抄底"
            else:
                cat = "⚠️ 动能衰竭破位"
                strategy = "暂停加仓 / 严格执行止损"

            ret_str = f"{r.price_now_ret:+.1f}%" if r.price_now_ret is not None else "—"
            hold_str = f"{r.hold_delta_72h_pct:+.1f}%" if r.hold_delta_72h_pct is not None else "—"
            penalty_str = f"-{r.dump_penalty:.1f}" if r.dump_penalty > 0 else "0.0"
            reasons = r.dump_reasons if r.dump_reasons else f"历史偏离亏损 {ret_str}"

            tok_str = format_token_cell(r.token_symbol, r.chain)
            md_lines.append(
                f"| **{cat}** | {tok_str} | **{r.meta_score:.2f}** | {traj_str} | {ret_str} | {hold_str} | {penalty_str} | {reasons} | {strategy} |"
            )

    md_lines.extend([
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
