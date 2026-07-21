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
        """全量代币通用版：《异常关注 - 60天全量代币突发拉伸诊断专报》"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        file_timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        filepath = os.path.join(self.output_dir, f"impulse_surge_{file_timestamp}.md")

        triggered_tokens = [r for r in surge_results if r.is_triggered]

        content = f"# 🚀 异常关注：60天全量代币《突发拉伸》诊断专报\n\n"
        content += f"> 生成时间: {now_str} | 扫描 60d 数据内全量代币 | 触发突发拉伸目标: {len(triggered_tokens)} 个\n\n"

        content += "## 一、 全量触发标的与 5 维边界条件判定矩阵\n\n"
        content += "| 序号 | 代币符号 | 代币合约地址 | 网络 | 流动性突变比率 (S1) | PnL 7d 斜率 (S2) | 巨鲸 7d 净流入 (S3) | 成交量脉冲比 (S4) | 72h 振荡翻转 (S5) | 触发条件归因 |\n"
        content += "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"

        for idx, r in enumerate(triggered_tokens, 1):
            addr = r.token_address
            trunc_addr = f"`{addr[:5]}...{addr[-6:]}`"
            span_addr = f'<span title="{addr}">{trunc_addr}</span>'
            reasons_str = "<br>".join(r.trigger_reasons)
            content += f"| {idx} | `{r.token_symbol}` | {span_addr} | `{r.chain}` | `{r.liq_ratio:.2f}` | `{r.pnl_slope_7d:.1f}` | `{r.whale_net_7d}` | `{r.vol_ratio:.2f}` | `{r.oscillation_cnt}次` | {reasons_str} |\n"

        content += "\n## 二、 全量突发拉伸代币多维触发分布汇总\n\n"
        s1_cnt = sum(1 for r in triggered_tokens if any("S1" in res for res in r.trigger_reasons))
        s2_cnt = sum(1 for r in triggered_tokens if any("S2" in res for res in r.trigger_reasons))
        s3_cnt = sum(1 for r in triggered_tokens if any("S3" in res for res in r.trigger_reasons))
        s4_cnt = sum(1 for r in triggered_tokens if any("S4" in res for res in r.trigger_reasons))
        s5_cnt = sum(1 for r in triggered_tokens if any("S5" in res for res in r.trigger_reasons))

        content += "| 维度标识 | 检测维度名称 | 全库触发标的数 | 维度判定核心含义 |\n"
        content += "| :--- | :--- | :--- | :--- |\n"
        content += f"| **S1** | 流动性结构突变 | {s1_cnt} 个 | reserve_usd 相比 14d 均值骤降 >75% |\n"
        content += f"| **S2** | PnL 斜率爆发 | {s2_cnt} 个 | 7d PnL 增长率 > 50.0 |\n"
        content += f"| **S3** | 巨鲸净流入爆发 | {s3_cnt} 个 | 7d 巨鲸净流入交易次数 > 5 次 |\n"
        content += f"| **S4** | 成交量脉冲倍率 | {s4_cnt} 个 | 3d 均量 / 14d 均量 > 2.0 倍 |\n"
        content += f"| **S5** | 判定振荡抑制 | {s5_cnt} 个 | 72h 内判定翻转次数 >= 3 次 |\n"

        # 如果有触发标的，生成前 5 个最强爆发标的对比甘特图
        top_targets = triggered_tokens[:5]
        if top_targets:
            content += "\n## 三、 前 5 大突发爆发标的检测响应时序\n\n"
            content += "```mermaid\n"
            content += "gantt\n"
            content += "    title 全量突发拉伸 Top5 爆发标的 5 维引擎响应时序图\n"
            content += "    dateFormat  YYYY-MM-DD\n"
            for t in top_targets:
                content += f"    section {t.token_symbol} ({t.chain})\n"
                content += f"    S1-S4 爆发告警 (PnL Slope={t.pnl_slope_7d:.1f}) :active, 2026-07-17, 2026-07-20\n"
                content += f"    S5 振荡防护 ({t.oscillation_cnt}次翻转)              :done, 2026-07-21, 2026-07-21\n"
            content += "```\n"

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        return filepath
