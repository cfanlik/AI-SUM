import os
import json
from datetime import datetime

class AnomalyReportGenerator:
    def __init__(self, output_dir="/opt/AI-SUM/report/anomaly"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def generate_report(self, pools):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        filepath = os.path.join(self.output_dir, f"anomaly_{timestamp}.md")
        
        micro_pools = [p for p in pools if p["status"] == "MICRO_CORE_POOL"]
        zombie_pools = [p for p in pools if p["status"] == "DEAD_ZOMBIE_POOL"]
        non_core_pools = [p for p in pools if p["status"] == "NON_CORE_PAIR"]
        valid_pools = [p for p in pools if p["status"] == "VALID_LARGE_POOL"]
        
        micro_pools.sort(key=lambda x: x["final_reserve"], reverse=True)
        
        content = f"# AI-SUM 异常关注 (Anomaly Watchlist) 专报\n\n"
        content += f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 总监控 CLAMM 池子: {len(pools)} 个\n\n"
        
        content += "## 一、物理解算物理汇总表\n\n"
        content += "| 防伪状态标识 | 物理定义 | 数量 (个) | 占比 (%) |\n"
        content += "| :--- | :--- | :--- | :--- |\n"
        content += f"| `VALID_LARGE_POOL` | 真实换手活跃大池 (≥$5000) | {len(valid_pools)} | {len(valid_pools)/len(pools)*100:.2f}% |\n"
        content += f"| `MICRO_CORE_POOL` | 核心有效微型池 (<$5000 具备换手) | {len(micro_pools)} | {len(micro_pools)/len(pools)*100:.2f}% |\n"
        content += f"| `DEAD_ZOMBIE_POOL` | 链上僵尸死池 (零换手覆盖为$0) | {len(zombie_pools)} | {len(zombie_pools)/len(pools)*100:.2f}% |\n"
        content += f"| `NON_CORE_PAIR` | 无价值山寨互刷池 (非硬通货) | {len(non_core_pools)} | {len(non_core_pools)/len(pools)*100:.2f}% |\n\n"
        
        content += "## 二、真实核心微型池明细表 (MICRO_CORE_POOL)\n\n"
        content += "| 序号 | 代币 (Symbol) | 代币合约 | DEX ID | 池子 ID (PoolID) | 交易对名称 | 真实储备金 (USD) | 24h 交易笔数 |\n"
        content += "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
        for i, p in enumerate(micro_pools, 1):
            content += f"| {i} | `{p['symbol']}` | `{p['token']}` | `{p['dex_id']}` | `{p['pool_id'][:12]}...` | {p['pair_name']} | ${p['final_reserve']:,.2f} | {p['total_tx']} 笔 |\n"
            
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
            
        return filepath
