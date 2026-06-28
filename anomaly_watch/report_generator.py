import os
from datetime import datetime

def short_addr(addr):
    if not addr or addr == "N/A": return "N/A"
    s = str(addr).strip()
    if len(s) > 12:
        return f"{s[:5]}...{s[-6:]}"
    return s

def format_hover_addr(addr):
    if not addr or addr == "N/A": return "N/A"
    s = str(addr).strip()
    short = short_addr(s)
    return f'<span title="{s}">`{short}`</span>'

class AnomalyReportGenerator:
    def __init__(self, output_dir="/opt/AI-SUM/report/anomaly"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def generate_report(self, token_results):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        filepath = os.path.join(self.output_dir, f"anomaly_{timestamp}.md")
        
        fake_liq_tokens = [t for t in token_results if "FAKE" in t["status"]]
        micro_tokens = [t for t in token_results if "MICRO" in t["status"]]
        
        fake_liq_tokens.sort(key=lambda x: x["raw_fake_val"], reverse=True)
        
        content = f"# AI-SUM 伪流动性陷阱与三大物理维度风控专报\n\n"
        content += f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 识别伪流动性陷阱代币: {len(fake_liq_tokens)} 个\n\n"
        
        content += "## 一、 🚨 重点关注：三大物理维度严谨核验与风控列表 (FAKE_ZERO_LIQUIDITY (> $10万))\n\n"
        content += "> **物理风控断言**：绝对物理拆分三大维度（维1：API标称/Active TVL，维2：24h成交流水，维3：RPC实测沉淀本金）。零换手伪流动性诱多陷阱将在 Meta 仲裁中物理扣除 -40 分（物理筛查起点: $100,000.00）。\n\n"
        content += "| 序号 | 代币名称 (Symbol) | 代币合约地址 | 池子地址 | 识别配对名称 | 维 1：API 理论虚拟标称 (Virtual Reserve / Active TVL) | 维 2：物理 24h 真实成交流水 (24h Volume) | 维 3：链上合约实际托管本金 (On-Chain Balance) | Meta 仲裁惩罚扣分 | 最新仲裁判定 (Verdict) | 当前生命周期阶段 (Stage) |\n"
        content += "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
        
        for i, t in enumerate(fake_liq_tokens, 1):
            t_addr_fmt = format_hover_addr(t['token'])
            p_addr_fmt = format_hover_addr(t.get('pool_id', 'N/A'))
            v1_str = f"${t['raw_fake_val']:,.2f}<br>*(修正Active TVL: ${t['active_tvl']:,.2f})*"
            v2_str = f"${t['vol_h24']:,.2f}"
            v3_str = f"**${t['onchain_usd']:,.2f}**" if t['onchain_usd'] > 0 else "$0.00"
            content += f"| {i} | `{t['symbol']}` | {t_addr_fmt} | {p_addr_fmt} | {t['fake_pair_name']} | {v1_str} | **{v2_str}** | **{v3_str}** | **{t['penalty']:.0f} 分** | `{t['meta_verdict']}` | `{t['stage']}` |\n"
            
        content += "\n## 二、核心有效微型代币备忘表 (MICRO_CORE_TOKENS)\n\n"
        content += "| 序号 | 代币名称 (Symbol) | 代币合约地址 | 仲裁综合得分 (Meta Score) | 最新仲裁判定 | 当前生命周期阶段 | 庄控等级 |\n"
        content += "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
        for i, t in enumerate(micro_tokens[:15], 1):
            score_str = f"{t['meta_score']:.1f}" if t['meta_score'] is not None else "-"
            t_addr_fmt = format_hover_addr(t['token'])
            content += f"| {i} | `{t['symbol']}` | {t_addr_fmt} | **{score_str}** | `{t['meta_verdict']}` | `{t['stage']}` | `{t['whale_level']}` |\n"
            
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
            
        return filepath
