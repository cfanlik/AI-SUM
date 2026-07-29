"""
DEX 庄家资金与暗流吸筹穿透分析器 (Anomaly Watch - Penetration Analysis)
包含 5 大物理防错算法 (BUG-1~5) - 包含 BUG-5 庄家真实浮盈率修正版
"""
from __future__ import annotations
import os
import json
import sqlite3
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any

logger = logging.getLogger("anomaly_penetration")

def get_db_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_penetration_table(sum_conn: sqlite3.Connection):
    """创建 dex_penetration_snapshots 持久化表"""
    sum_conn.execute("""
        CREATE TABLE IF NOT EXISTS dex_penetration_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time TEXT NOT NULL,
            token_address TEXT NOT NULL,
            chain TEXT NOT NULL,
            token_symbol TEXT,
            swap_usd_real REAL,            -- BUG-2 修复后 USD
            swap_token_units REAL,         -- 原始代币数量
            swap_lp_ratio REAL,            -- swap_usd_real / reserve_usd * 100
            boss_dispatched_usd REAL,      -- BUG-1 entity_id 去重后注资
            boss_entity_count INTEGER,     -- 独立实体数
            boss_avg_control REAL,         -- 控盘均分
            boss_collision_count INTEGER,  -- BUG-3 排噪后碰撞庄家地址数
            avg_cost_usd REAL,             -- BUG-5 修正后的单币买入成本单价
            unrealized_pnl_pct REAL,       -- BUG-5 修正后的庄家真实浮盈率
            inbound_top_source TEXT,       -- 上游最大资金来源地址
            price_usd REAL,
            reserve_usd REAL,
            UNIQUE(token_address, chain, scan_time)
        )
    """)
    sum_conn.commit()

def run_penetration_analysis(sum_db_path: str = "/opt/AI-SUM/select-sum.db",
                            src_db_path: str = "/opt/select-coin/data/select.db",
                            scan_time: Optional[str] = None) -> List[Dict[str, Any]]:
    if not scan_time:
        scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    sum_conn = get_db_connection(sum_db_path)
    src_conn = get_db_connection(src_db_path)
    ensure_penetration_table(sum_conn)

    # 查前 30 个核心监控代币
    tokens_rows = sum_conn.execute("SELECT DISTINCT chain, token_address, token_symbol FROM watchlist LIMIT 30").fetchall()
    if not tokens_rows:
        tokens_rows = sum_conn.execute("SELECT DISTINCT chain, token_address, token_symbol FROM meta_snapshots ORDER BY id DESC LIMIT 30").fetchall()

    # 提取最新 batch_id
    batch_row = src_conn.execute("SELECT MAX(batch_id) as latest_batch FROM bubblemap_holders").fetchone()
    latest_batch = batch_row["latest_batch"] if batch_row else None

    if not latest_batch:
        logger.warning("[Penetration] 未找到 bubblemap_holders 最新批次")
        src_conn.close()
        sum_conn.close()
        return []

    # 预加载通用噪点地址 (出现在 >= 50 个代币中的庄家地址)
    noise_addrs = set()
    try:
        cur_n = src_conn.execute("SELECT address FROM boss_cross_tokens GROUP BY address HAVING COUNT(DISTINCT token_address) >= 50")
        noise_addrs = {r["address"] for r in cur_n.fetchall()}
    except Exception:
        pass

    analyzed_results: List[Dict[str, Any]] = []

    for row in tokens_rows:
        t_addr = row["token_address"]
        chain = row["chain"] or "bsc"
        symbol = row["token_symbol"] or t_addr[:8]

        # 查现价与池子深度
        price_row = src_conn.execute("""
            SELECT price_usd, reserve_usd FROM gecko_market_data
            WHERE token_address = ? AND price_usd > 0
            ORDER BY scan_time DESC LIMIT 1
        """, (t_addr,)).fetchone()
        price_usd = price_row["price_usd"] if price_row else 0.0
        reserve_usd = price_row["reserve_usd"] if price_row else 0.0

        # BUG-2 物理防错计算：swap_in_value * price_usd (全表 Token Units 折算 + LP Cap)
        # BUG-5 物理防错计算：读取 gmgn_buy_cost_usd 与 gmgn_buy_amount 计算真实单币买入成本单价
        flow_row = src_conn.execute("""
            SELECT SUM(swap_in_value) as raw_units,
                   SUM(gmgn_buy_cost_usd) as total_cost_usd,
                   SUM(gmgn_buy_amount) as total_buy_amount,
                   MAX(inbound_sources) as sample_inbound
            FROM bubblemap_holders
            WHERE token_address = ? AND batch_id = ?
              AND is_accumulating = 1 AND is_cex = 0 AND is_dex = 0 AND is_contract = 0
        """, (t_addr, latest_batch)).fetchone()

        raw_units = flow_row["raw_units"] if flow_row and flow_row["raw_units"] else 0.0
        swap_usd_real = (raw_units * price_usd) if price_usd > 0 else None
        if swap_usd_real and reserve_usd > 0:
            swap_usd_real = min(swap_usd_real, reserve_usd * 5.0)

        swap_lp_ratio = (swap_usd_real / reserve_usd * 100.0) if (swap_usd_real and reserve_usd > 0) else 0.0

        # BUG-5 修正算法：加权平均单币买入成本单价 unit_cost
        total_cost_usd = flow_row["total_cost_usd"] if flow_row and flow_row["total_cost_usd"] else 0.0
        total_buy_amount = flow_row["total_buy_amount"] if flow_row and flow_row["total_buy_amount"] else 0.0

        unit_cost = (total_cost_usd / total_buy_amount) if (total_cost_usd > 0 and total_buy_amount > 0) else None
        unrealized_pnl_pct = ((price_usd - unit_cost) / unit_cost * 100.0) if (price_usd > 0 and unit_cost and unit_cost > 0) else None

        # BUG-1 物理防错计算：entity_id 分组去重取 MAX 团伙资金
        fund_row = src_conn.execute("""
            SELECT SUM(max_d) as total_dispatched, COUNT(*) as entity_cnt, AVG(max_c) as avg_control
            FROM (
                SELECT COALESCE(entity_id, wallet_address) as gid,
                       MAX(associated_dispatched_usd) as max_d,
                       MAX(associated_control_score) as max_c
                FROM bubblemap_holders
                WHERE token_address = ? AND batch_id = ?
                  AND associated_dispatched_usd > 0
                GROUP BY gid
            ) t
        """, (t_addr, latest_batch)).fetchone()

        dispatched_usd = fund_row["total_dispatched"] if fund_row and fund_row["total_dispatched"] else 0.0
        entity_cnt = fund_row["entity_cnt"] if fund_row else 0
        avg_control = fund_row["avg_control"] if fund_row and fund_row["avg_control"] else 0.0

        # BUG-3 物理防错计算：boss_cross_tokens 噪点排除
        boss_addrs_rows = src_conn.execute("""
            SELECT DISTINCT bct.address
            FROM bubblemap_holders bh
            INNER JOIN boss_cross_tokens bct ON bh.wallet_address = bct.address
            WHERE bh.token_address = ? AND bh.batch_id = ?
              AND bh.is_accumulating = 1 AND bh.is_cex = 0 AND bh.is_dex = 0 AND bh.is_contract = 0
        """, (t_addr, latest_batch)).fetchall()

        boss_hits = 0
        for b_r in boss_addrs_rows:
            if b_r["address"] not in noise_addrs:
                boss_hits += 1

        # 解析资金来源
        top_inbound_source = None
        if flow_row and flow_row["sample_inbound"]:
            try:
                sources = json.loads(flow_row["sample_inbound"])
                if isinstance(sources, dict) and sources:
                    top_inbound_source = max(sources.items(), key=lambda x: x[1])[0]
            except Exception:
                pass

        res_item = {
            "scan_time": scan_time,
            "token_address": t_addr,
            "chain": chain,
            "token_symbol": symbol,
            "swap_usd_real": swap_usd_real,
            "swap_token_units": raw_units,
            "swap_lp_ratio": round(swap_lp_ratio, 2),
            "boss_dispatched_usd": dispatched_usd,
            "boss_entity_count": entity_cnt,
            "boss_avg_control": round(avg_control, 2),
            "boss_collision_count": boss_hits,
            "avg_cost_usd": unit_cost,
            "unrealized_pnl_pct": round(unrealized_pnl_pct, 2) if unrealized_pnl_pct is not None else None,
            "inbound_top_source": top_inbound_source,
            "price_usd": price_usd,
            "reserve_usd": reserve_usd
        }
        analyzed_results.append(res_item)

        sum_conn.execute("""
            INSERT OR REPLACE INTO dex_penetration_snapshots
            (scan_time, token_address, chain, token_symbol, swap_usd_real, swap_token_units,
             swap_lp_ratio, boss_dispatched_usd, boss_entity_count, boss_avg_control,
             boss_collision_count, avg_cost_usd, unrealized_pnl_pct, inbound_top_source,
             price_usd, reserve_usd)
            VALUES (:scan_time, :token_address, :chain, :token_symbol, :swap_usd_real, :swap_token_units,
                    :swap_lp_ratio, :boss_dispatched_usd, :boss_entity_count, :boss_avg_control,
                    :boss_collision_count, :avg_cost_usd, :unrealized_pnl_pct, :inbound_top_source,
                    :price_usd, :reserve_usd)
        """, res_item)

    sum_conn.commit()
    src_conn.close()
    sum_conn.close()

    export_penetration_markdown(analyzed_results, scan_time)
    return analyzed_results

def export_penetration_markdown(results: List[Dict[str, Any]], scan_time: str):
    """导出 Markdown 穿透专报物理文件"""
    dt_str = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir = "/opt/AI-SUM/report/anomaly/penetration"
    os.makedirs(out_dir, exist_ok=True)
    file_path = os.path.join(out_dir, f"penetration_analysis_{dt_str}.md")
    latest_path = os.path.join(out_dir, "latest_penetration_analysis.md")

    sorted_results = sorted(results, key=lambda x: (x["swap_usd_real"] or 0.0), reverse=True)

    lines = []
    lines.append("# 🔍 异常关注 — DEX 庄家资金穿透分析专报")
    lines.append(f"\n> **生成时间**：{scan_time} | 算法版本：damf_v8 (BUG-1~5 物理打靶修复版)")
    lines.append("> ⚠ `swap_in_value` 已无条件全表乘以 price_usd 折算为 USD；`unrealized_pnl_pct` 已修正为加权单币成本单价计算。\n")
    lines.append("---")
    lines.append("\n## 🎯 一、 DEX 真实资金净流入与池子穿透排行 (Top 10)")
    lines.append("\n| 排名 | 代币 | 链 | 现价 | 真实 DEX 纯买入USD | 资金池深度 (LP) | 纯买入/LP 比率 | 庄家买入单价 | 庄家真实浮盈率 | 异常建仓诊断 |")
    lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")

    idx = 1
    for r in sorted_results[:10]:
        swap_str = f"${r['swap_usd_real']:,.2f}" if r['swap_usd_real'] is not None else "N/A"
        cost_str = f"${r['avg_cost_usd']:.4f}" if r['avg_cost_usd'] is not None else "N/A"
        pnl_str = f"{r['unrealized_pnl_pct']:+.2f}%" if r['unrealized_pnl_pct'] is not None else "N/A"
        diag = "🔴 极端资金穿透池子建仓" if r['swap_lp_ratio'] > 30 else ("🟡 强力 DEX 现货买入" if r['swap_lp_ratio'] > 10 else "🟢 静默建仓")
        lines.append(f"| **No.{idx}** | {r['token_symbol']} | `{r['chain']}` | ${r['price_usd']:.4f} | **{swap_str}** | ${r['reserve_usd']:,.2f} | **{r['swap_lp_ratio']:.1f}%** | {cost_str} | **{pnl_str}** | {diag} |")
        idx += 1

    lines.append("\n---")
    lines.append("\n## 🏦 二、 庄家团伙真实注资与控盘分析 (entity_id 去重归集)")
    lines.append("\n| 代币 | 团伙去重后资金规模 | 独立实体数 | 控盘均分 | 资金来源归集 |")
    lines.append("| :--- | :--- | :--- | :--- | :--- |")

    for r in sorted_results[:10]:
        disp_str = f"${r['boss_dispatched_usd']:,.2f}" if r['boss_dispatched_usd'] > 0 else "$0.00"
        src_str = f"`{r['inbound_top_source'][:16]}...`" if r['inbound_top_source'] else "未知"
        lines.append(f"| {r['token_symbol']} | **{disp_str}** | {r['boss_entity_count']} 个独立实体 | **{r['boss_avg_control']:.1f}** | {src_str} |")

    lines.append("\n---")
    lines.append("\n## 🔗 三、 庄家跨代币网络穿透共振 (排噪后)")
    lines.append("\n| 代币 | 排噪后庄家共振数 | 共振诊断 |")
    lines.append("| :--- | :--- | :--- |")
    for r in sorted_results[:10]:
        lines.append(f"| {r['token_symbol']} | **{r['boss_collision_count']} 个庄家节点** | 跨代币拓扑共振联动 |")

    content = "\n".join(lines)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    with open(latest_path, "w", encoding="utf-8") as f:
        f.write(content)

    logger.info(f"[Penetration] 专报生成成功: {file_path}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_penetration_analysis()
