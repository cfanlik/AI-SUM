import os
from datetime import datetime

class AnomalyReportGenerator:
    def __init__(self):
        self.output_dir = "/opt/AI-SUM/report/anomaly"
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir, exist_ok=True)

    def generate_report(self, token_results):
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

    def generate_impulse_surge_report(self, surge_results):
        """生成《异常关注 - 突发拉伸（60天周期）专题报告》"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        file_timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        filepath = os.path.join(self.output_dir, f"impulse_surge_{file_timestamp}.md")

        triggered_tokens = [r for r in surge_results if r.is_triggered]

        content = f"# 🚀 异常关注：60天周期《突发拉伸》专题诊断报告\n\n"
        content += f"> 生成时间: {now_str} | 扫描 60d 数据标的 | 触发突发拉伸目标: {len(triggered_tokens)} 个\n\n"

        content += "## 一、 核心触发标的与 5 维边界条件判定矩阵\n\n"
        content += "| 序号 | 代币符号 | 代币合约地址 | 流动性突变比率 (S1) | PnL 7d 斜率 (S2) | 巨鲸 7d 净流入 (S3) | 成交量脉冲比 (S4) | 72h 振荡翻转 (S5) | 触发条件归因 |\n"
        content += "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"

        for idx, r in enumerate(triggered_tokens, 1):
            addr = r.token_address
            trunc_addr = f"`{addr[:5]}...{addr[-6:]}`"
            span_addr = f'<span title="{addr}">{trunc_addr}</span>'
            reasons_str = "<br>".join(r.trigger_reasons)
            content += f"| {idx} | `{r.token_symbol}` | {span_addr} | `{r.liq_ratio:.2f}` | `{r.pnl_slope_7d:.1f}` | `{r.whale_net_7d}` | `{r.vol_ratio:.2f}` | `{r.oscillation_cnt}次` | {reasons_str} |\n"

        content += "\n## 二、 走势图例与拟合对比 (以 BSC BANK 示例)\n\n"
        content += "```mermaid\n"
        content += "gantt\n"
        content += "    title BSC BANK 链上动作与 5 维边界条件触发时序图\n"
        content += "    dateFormat  YYYY-MM-DD\n"
        content += "    section 物理链上持仓 (Arkham)\n"
        content += "    缓步吸筹 (2.3M->2.55M)           :active, a1, 2026-06-26, 2026-07-16\n"
        content += "    突发拉伸峰值 (2.55M->2.95M)       :crit, a2, 2026-07-17, 2026-07-20\n"
        content += "    急速砸盘转出 (2.95M->2.22M)       :done, a3, 2026-07-21, 2026-07-21\n"
        content += "    section 5 维突发拉伸检测引擎\n"
        content += "    S1 流动性骤降预警 (0.094)        :crit, b1, 2026-07-16, 2026-07-16\n"
        content += "    S2+S4 突发拉伸高分告警            :active, b2, 2026-07-17, 2026-07-20\n"
        content += "    S5 振荡抑制 + DEATH_SPIRAL 风控  :done, b3, 2026-07-21, 2026-07-21\n"
        content += "```\n"

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        return filepath
