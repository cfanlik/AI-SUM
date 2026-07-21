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
        """Top 10 精简排版版：《异常关注 - 60天 4阶PnL动量矩阵突发拉伸诊断专报》"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        file_timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        filepath = os.path.join(self.output_dir, f"impulse_surge_{file_timestamp}.md")

        # 仅精简提取前 10 个最强爆发标的，物理消灭噪音
        triggered_tokens = [r for r in surge_results if r.is_triggered][:10]

        content = f"# 🚀 异常关注：60天 4阶PnL动量矩阵《突发拉伸》诊断专报\n\n"
        content += f"> 生成时间: {now_str} | 扫描 60d 全库数据 | 精准输出 Top 10 强爆发标的 (已剔除信息噪音)\n\n"

        content += "## 一、 符合指标 Top 10 强爆发标的与 4 阶 PnL 动量矩阵表\n\n"
        
        # 带有 HTML span title 浮动提示的表头列名
        content += "| <span title=\"爆发排名顺序，按 PnL 7d 增长斜率降序排列\">排名</span> "
        content += "| <span title=\"点击代币符号可于右侧详情抽屉查看 60 天持仓与 PnL 梯度变化折线图\">代币符号 (点击看图表)</span> "
        content += "| <span title=\"代币所属物理区块链网络 (如 bsc, eth)\">网络</span> "
        content += "| <span title=\"代币当前最新 PnL 盈亏比例 (%)\">PnL_now</span> "
        content += "| <span title=\"近 7 天内 PnL 累计增长率变化斜率\">7d斜率 ($S_{7d}$)</span> "
        content += "| <span title=\"近 15 天内 PnL 累计增长率变化斜率\">15d斜率 ($S_{15d}$)</span> "
        content += "| <span title=\"近 30 天内 PnL 累计增长率变化斜率\">30d斜率 ($S_{30d}$)</span> "
        content += "| <span title=\"近 60 天内 PnL 累计增长率变化斜率\">60d斜率 ($S_{60d}$)</span> "
        content += "| <span title=\"结合 4 阶斜率矩阵判定的多动量拉伸形态分类\">多阶矩阵形态</span> "
        content += "| <span title=\"S1-S5 五维拉伸与风控震荡防护综合诊断结论\">物理诊断结论</span> |\n"
        
        content += "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"

        for idx, r in enumerate(triggered_tokens, 1):
            reasons_str = "<br>".join(r.trigger_reasons)
            
            pat_tag = f"`{r.pattern}`"
            if r.pattern == "ACCELERATING_SURGE":
                pat_tag = "**🚀 凹向加速主升浪**"
            elif r.pattern == "STABLE_HIGH_SURGE":
                pat_tag = "**🔥 高位强平台拉伸**"
            elif r.pattern == "GENERAL_SURGE":
                pat_tag = "**📈 普通震荡拉升**"

            content += f"| **No.{idx}** | `{r.token_symbol}` | `{r.chain}` | `{r.pnl_now:.1f}` | `{r.slope_7d:.1f}` | `{r.slope_15d:.1f}` | `{r.slope_30d:.1f}` | `{r.slope_60d:.1f}` | {pat_tag} | {reasons_str} |\n"

        content += "\n## 二、 全量突发拉伸代币 5 维触发分布统计\n\n"
        all_triggered = [r for r in surge_results if r.is_triggered]
        s1_cnt = sum(1 for r in all_triggered if any("S1" in res for res in r.trigger_reasons))
        s2_cnt = sum(1 for r in all_triggered if any("S2" in res for res in r.trigger_reasons))
        s3_cnt = sum(1 for r in all_triggered if any("S3" in res for res in r.trigger_reasons))
        s4_cnt = sum(1 for r in all_triggered if any("S4" in res for res in r.trigger_reasons))
        s5_cnt = sum(1 for r in all_triggered if any("S5" in res for res in r.trigger_reasons))

        content += "| <span title=\"五维判断维度标识\">维度标识</span> | <span title=\"五维检测核心名称\">检测维度名称</span> | <span title=\"全库触发该维度的代币数量\">全库触发标的数</span> | <span title=\"该维度的物理解释说明\">维度判定物理含义</span> |\n"
        content += "| :--- | :--- | :--- | :--- |\n"
        content += f"| **S1** | 流动性结构突变 | {s1_cnt} 个 | reserve_usd 相比 14d 均值骤降 >75% |\n"
        content += f"| **S2** | 4 阶 PnL 动量爆发 | {s2_cnt} 个 | 7d PnL 增长斜率 S7d > 50.0 |\n"
        content += f"| **S3** | 巨鲸净流入爆发 | {s3_cnt} 个 | 7d 巨鲸净流入交易次数 > 5 次 |\n"
        content += f"| **S4** | 成交量脉冲倍率 | {s4_cnt} 个 | 3d 均量 / 14d 均量 > 2.0 倍 |\n"
        content += f"| **S5** | 判定振荡抑制 | {s5_cnt} 个 | 72h 内 verdict 翻转次数 >= 3 次 |\n"

        # 前 10 大爆发标的原生字符对比度展示 (100% 可读，绝不写死包含```mermaid)
        if triggered_tokens:
            content += "\n## 三、 前 10 大突发爆发标的脉冲强度与振荡防护 (100% 原生 Markdown 排版)\n\n"
            content += "| <span title=\"爆发排名顺序\">排名</span> | <span title=\"代币符号\">代币符号</span> | <span title=\"网络\">网络</span> | <span title=\"当前PnL\">当前 PnL</span> | <span title=\"7d PnL 增长脉冲字符对比条\">7d 爆发脉冲强度 (原生字符对比)</span> | <span title=\"72h 判定振荡防护拦截状态\">72h 判定振荡防护状态</span> | <span title=\"综合诊断结论\">核心诊断结论</span> |\n"
            content += "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
            
            max_s7 = max(abs(t.slope_7d) for t in triggered_tokens) if triggered_tokens else 1.0
            for idx, t in enumerate(triggered_tokens, 1):
                bar_len = min(10, max(1, int((abs(t.slope_7d) / max(100.0, max_s7)) * 10)))
                bar_str = "[" + "█" * bar_len + "░" * (10 - bar_len) + "]"
                
                prot_str = f"**[已激活]** {t.oscillation_cnt}次翻转拦截" if t.oscillation_cnt >= 3 else f"[未激活] {t.oscillation_cnt}次翻转"
                
                content += f"| **No.{idx}** | `{t.token_symbol}` | `{t.chain}` | `{t.pnl_now:.1f}` | `{bar_str} {t.slope_7d:.1f}` | {prot_str} | S1-S5 多维综合触发 |\n"

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        return filepath
