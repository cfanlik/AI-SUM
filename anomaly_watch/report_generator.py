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
        """Top 10 单一精炼主表版：《异常关注 - 60天 4阶PnL动量矩阵突发拉伸诊断专报》"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        file_timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        filepath = os.path.join(self.output_dir, f"impulse_surge_{file_timestamp}.md")

        # 仅精简提取前 10 个最强爆发标的，物理消灭噪音
        triggered_tokens = [r for r in surge_results if r.is_triggered][:10]

        content = f"# 🚀 AI-SUM 60天 4阶PnL动量矩阵《突发拉伸》风控专报\n\n"
        content += f"> 生成时间: {now_str} | 扫描 60d 全库数据 | 精准输出 Top 10 强爆发标的 (无噪音单表版)\n\n"

        # 顶部集中声明 Tips 引用块 (彻底解决表头源码裸露)
        content += "> **物理指标 Tips 与断言说明**：\n"
        content += "> - **PnL_now**: 代币最新 PnL 盈亏比率 (%)。\n"
        content += "> - **4阶 PnL 斜率矩阵 ($S_{7d}/S_{15d}/S_{30d}/S_{60d}$)**: 7天/15天/30天/60天内 PnL 累计增长斜率，用于识别凹向加速主升浪与拦截坑底死猫跳反弹。\n"
        content += "> - **7d 爆发脉冲强度**: 7d 内 PnL 累计增长斜率及可视化字符对比条（`[██████████]`）。\n"
        content += "> - **72h 判定振荡防护状态**: S5 维 72h 内判定翻转次数与抖动拦截激活状态。\n"
        content += "> - **多阶矩阵形态**: 结合 4 阶斜率确定的动量形态（`🚀 凹向加速主升浪`、`🔥 高位强平台拉伸`、`📈 普通震荡拉升`）。\n"
        content += "> - **综合诊断归因**: S1(流动性突降)、S2(4阶PnL动量)、S3(巨鲸净流入)、S4(成交量脉冲)、S5(72h振荡防护) 综合触发原因。\n\n"

        # 全局唯一 Top 10 精炼主表 (表头 100% 纯净无 HTML 属性标签)
        content += "| 排名 | 代币名称<br>(Symbol) | 网络 | 当前 PnL | 4 阶 PnL 斜率矩阵<br>(S7d / S15d / S30d / S60d) | 7d 爆发脉冲强度 | 72h 判定振荡防护状态 | 多阶矩阵形态 | 综合诊断归因 |\n"
        content += "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"

        max_s7 = max(abs(t.slope_7d) for t in triggered_tokens) if triggered_tokens else 1.0

        for idx, r in enumerate(triggered_tokens, 1):
            reasons_str = "<br>".join(r.trigger_reasons)
            
            pnl_matrix_str = f"**`{r.slope_7d:.1f}`** / **`{r.slope_15d:.1f}`** / **`{r.slope_30d:.1f}`** / **`{r.slope_60d:.1f}`**"

            pat_tag = f"`{r.pattern}`"
            if r.pattern == "ACCELERATING_SURGE":
                pat_tag = "**🚀 凹向加速主升浪**"
            elif r.pattern == "STABLE_HIGH_SURGE":
                pat_tag = "**🔥 高位强平台拉伸**"
            elif r.pattern == "GENERAL_SURGE":
                pat_tag = "**📈 普通震荡拉升**"

            # 字符脉冲对比条
            bar_len = min(10, max(1, int((abs(r.slope_7d) / max(100.0, max_s7)) * 10)))
            bar_str = "`[" + "█" * bar_len + "░" * (10 - bar_len) + f"] {r.slope_7d:.1f}`"

            # 72h 振荡防护状态
            prot_str = f"**[已激活]** {r.oscillation_cnt}次翻转拦截" if r.oscillation_cnt >= 3 else f"[未激活] {r.oscillation_cnt}次翻转"

            content += f"| **No.{idx}** | `{r.token_symbol}` | `{r.chain}` | `{r.pnl_now:.1f}` | {pnl_matrix_str} | {bar_str} | {prot_str} | {pat_tag} | {reasons_str} |\n"

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        return filepath
