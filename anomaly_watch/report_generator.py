import os, math
from datetime import datetime

class AnomalyReportGenerator:
    def __init__(self):
        self.output_dir = "/opt/AI-SUM/report/anomaly"
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir, exist_ok=True)

    def generate_report(self, token_results):
        """原有的专报 A：保持绝对独立存在（《AI-SUM 伪流动性陷阱与四大物理维度风控专报》）"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        file_timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        filepath = os.path.join(self.output_dir, f"anomaly_{file_timestamp}.md")

        fake_liq_tokens = [t for t in token_results if t.get("status") == "FAKE_ZERO_LIQUIDITY"]
        fake_liq_tokens.sort(key=lambda x: x["raw_fake_val"], reverse=True)

        micro_core_tokens = [t for t in token_results if t.get("status") == "MICRO_CORE_TOKEN"]

        content = f"# AI-SUM 伪流动性陷阱与四大物理维度风控专报\n\n"
        content += f"> 生成时间: {now_str} | 识别伪流动性陷阱代币: {len(fake_liq_tokens)} 个\n\n"

        content += "## 一、 🚨 重点关注：四大物理维度严谨核验与风控列表 (FAKE_ZERO_LIQUIDITY (> $10万标称 且 托管 < $1万或占比 < 11.7%))\n\n"
        content += "> **物理风控断言**：绝对物理拆分四大维度（维1：API理论名义价值，维2：24h成交流水，维3：RPC链上实测托管本金）。零换手伪流动性诱多陷阱将在 Meta 仲裁中物理扣除 -40 分（物理筛查门槛: 维1名义 >= $100,000.00 且 维3实测托管 < $10,000.00 或占比 < 11.7%）。\n\n"

        content += "| 序号 | 代币名称<br>(Symbol) | 代币合约地址 | 池子地址 | 识别配对名称 | 维 1：API 理论名义价值 | 维 2：24h 流水 | 维 3：链上合约实际托管本金 |\n"
        content += "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"

        for idx, t in enumerate(fake_liq_tokens, 1):
            addr = t['token']
            trunc_addr = f"`{addr[:5]}...{addr[-6:]}`"
            span_addr = f'<span title="{addr}">{trunc_addr}</span>'
            
            p_id = t.get('pool_id', 'N/A')
            trunc_pool = f"`{p_id[:5]}...{p_id[-6:]}`" if p_id != 'N/A' else 'N/A'
            span_pool = f'<span title="{p_id}">{trunc_pool}</span>' if p_id != 'N/A' else 'N/A'

            dim1_str = f"**${t['active_tvl']:,.2f}**"
            dim2_str = f"${t['vol_h24']:,.2f}"
            dim3_str = f"**${t['onchain_usd']:,.2f}**"

            content += f"| {idx} | `{t['symbol']}` | {span_addr} | {span_pool} | {t['fake_pair_name']} | {dim1_str} | {dim2_str} | {dim3_str} |\n"

        content += "\n## 二、 核心有效微型代币备忘表 (MICRO_CORE_TOKENS)\n\n"
        content += "| 序号 | 代币名称<br>(Symbol) | 代币合约地址 | 仲裁综合得分<br>(Meta Score) | 维 4：最新仲裁判定<br>(Verdict) | 维 4：当前生命周期阶段<br>(Stage) | 维 4：庄控等级<br>(Whale Level) |\n"
        content += "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"

        for idx, t in enumerate(micro_core_tokens, 1):
            addr = t['token']
            trunc_addr = f"`{addr[:5]}...{addr[-6:]}`"
            span_addr = f'<span title="{addr}">{trunc_addr}</span>'
            score_str = f"**{t['meta_score']:.1f}**" if t['meta_score'] is not None else "**-**"
            verdict_str = f"`{t['meta_verdict']}`"
            stage_str = f"`{t['stage']}`"
            whale_str = f"`{t['whale_level']}`" if t['whale_level'] != 'N/A' else '`-`'
            content += f"| {idx} | `{t['symbol']}` | {span_addr} | {score_str} | {verdict_str} | {stage_str} | {whale_str} |\n"

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        return filepath

    def generate_periodic_impulse_surge_report(self, analysis_run):
        """生成真实数据库吸筹 Top10 报告；融合老版本全量数据与视觉标识提示。"""
        now_str = analysis_run.generated_at
        file_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(
            self.output_dir,
            f"anomaly_periodic_impulse_surge_{file_timestamp}.md",
        )

        def normalize_100_cap(slope):
            if slope is None or slope <= 0:
                return 0.0
            score = 100.0 * (1.0 - math.exp(-slope / 100.0))
            return min(100.0, max(0.0, score))

        def pct(value):
            return "N/A" if value is None else f"{value * 100:+.1f}%"

        def rho_tag(value):
            if value is None:
                return "N/A"
            pct_str = f"{value * 100:+.1f}%"
            if value >= 0.8:
                return f"`{pct_str}` **[强正相关]**"
            elif value >= 0.6:
                return f"`{pct_str}` **[正相关]**"
            elif value >= 0.0:
                return f"`{pct_str}` [弱正相关]"
            else:
                return f"`{pct_str}` [负相关]"

        def amount(value):
            if value is None:
                return "N/A"
            if abs(value) >= 1e8:
                return f"{value / 1e8:.2f}亿"
            if abs(value) >= 1e6:
                return f"{value / 1e6:.2f}M"
            if abs(value) >= 1e3:
                return f"{value / 1e3:.1f}K"
            return f"{value:.0f}"

        content = "# 🚀 周期性突发吸筹风控专报\n\n"
        content += (
            f"> 生成时间: {now_str} | run_id: `{analysis_run.run_id}` | "
            f"全库候选 {analysis_run.candidate_count} 个 | 质量门禁通过 "
            f"{analysis_run.quality_pass_count} 个 | 输出真实吸筹 Top {len(analysis_run.top10)}\n\n"
        )
        content += "> **物理指标 Tips 说明**：\n"
        content += "> - **Top10 来源**：最近 48 小时全局真实 Bubblemaps 吸筹候选，先计算持币序列与 `ρ`，再进行质量门禁和排序。\n"
        content += "> - **固定队列人数 / 持币量**：最新一天 `max_acc` 最优快照中的固定吸筹地址集合，以及该集合最新去重持币数量（Token Units）。\n"
        content += "> - **60天持币正相关性 (`ρ`)**：固定队列每日持币量 `H(t)` 与时间 `t` 的 Pearson 相关系数（带分类提示）。\n"
        content += "> - **PnL (S7d/S15d/S30d/S60d)**：7d/15d/30d/60d 4 阶 PnL 斜率的 100 分封顶归一化得分。\n"
        content += "> - **72h 判定振荡防护状态**：S5 维度 72h 内判定翻转次数与抖动拦截激活状态。\n"
        content += "> - **7d/60d变化**：固定队列真实持币量首尾变化。\n\n"

        content += "| 排名 | 代币名称 | 网络 | 当前 PnL | PnL (S7d/S15d/S30d/S60d) | 72h 判定振荡防护状态 | 固定队列人数 | 最新持币量 | 60天持币正相关性 (ρ) | 7d变化 | 60d变化 | 最大单日变化 | 多阶矩阵形态 | S1–S5解释 |\n"
        content += "| :--- | :--- | :--- | :--- | :--- | :--- | ---: | ---: | :--- | ---: | ---: | :--- | :--- | :--- |\n"

        for row in analysis_run.top10:
            surge = row.surge_result
            if surge is not None:
                slopes = [
                    normalize_100_cap(getattr(surge, name, None))
                    for name in ("slope_7d", "slope_15d", "slope_30d", "slope_60d")
                ]
                pnl_matrix = " / ".join(f"**`{value:.1f}`**" for value in slopes)
                oscillation = int(getattr(surge, "oscillation_cnt", 0) or 0)
                protection = (
                    f"**[已激活]** {oscillation}次翻转拦截"
                    if oscillation >= 3
                    else f"[未激活] {oscillation}次翻转"
                )
                pattern = getattr(surge, "pattern", "GENERAL_SURGE")
                pattern_text = {
                    "ACCELERATING_SURGE": "**🚀 凹向爆发加速浪**",
                    "STABLE_HIGH_SURGE": "**🔥 高位强平台拉伸**",
                    "RISING_ACCUMULATION": "**⚡ 温和主升蓄势**",
                    "DEAD_CAT_BOUNCE": "**⚠️ 坑底死猫跳反弹**",
                    "GENERAL_SURGE": "**📈 普通震荡拉升**",
                }.get(pattern, f"`{pattern}`")
                reasons = "<br>".join(getattr(surge, "trigger_reasons", []) or []) or "未触发"
            else:
                pnl_matrix = "0.0 / 0.0 / 0.0 / 0.0"
                protection = "[未激活] 0次翻转"
                pattern_text = "**📈 普通震荡拉升**"
                reasons = "未触发（仅作解释）"

            pnl_text = "N/A" if row.pnl_ratio is None else f"{row.pnl_ratio:+.1f}%"
            max_daily = pct(row.max_daily_change)
            if row.max_daily_change_date:
                max_daily += f" ({row.max_daily_change_date})"
            token_cell = (
                f'<span data-run-id="{analysis_run.run_id}" '
                f'data-chain="{row.chain}" '
                f'data-token-address="{row.token_address}">{row.token_symbol}</span>'
            )
            content += (
                f"| **No.{row.rank}** | {token_cell} | `{row.chain}` | **`{pnl_text}`** | "
                f"{pnl_matrix} | {protection} | **`{row.acc_count}` 人** | "
                f"**`{amount(row.latest_hold_amount)}` 币** | {rho_tag(row.rho_60d)} | "
                f"`{pct(row.change_7d)}` | `{pct(row.change_60d)}` | {max_daily} | "
                f"{pattern_text} | {reasons} |\n"
            )

        if analysis_run.risk_rows:
            content += "\n## 数据质量/风险附表\n\n"
            content += "| 代币 | 网络 | ρ | 固定队列人数 | 未入榜原因 |\n"
            content += "| :--- | :--- | ---: | ---: | :--- |\n"
            for row in analysis_run.risk_rows:
                content += (
                    f"| {row.token_symbol} | `{row.chain}` | `{pct(row.rho_60d)}` | "
                    f"{row.acc_count} | `{','.join(row.risk_flags)}` |\n"
                )

        with open(filepath, "w", encoding="utf-8") as handle:
            handle.write(content)
        return filepath
