"""
hop2_whale_scanner.py
Meta-Verdict 决策驾驶舱第七屏子模块：Hop-2 间接巨额资金穿透雷达

功能:
1. 穿透 BubbleMap Top 300 持币大户的二跳资金路由 (Hop-2, dex_ratio_hop2 >= 0.5)。
2. 过滤真金门槛 (有效美元估值 >= min_usd, 默认 $10,000.0)，拒绝无声粉尘。
   - 严禁将数据库中未经转换的代币数量列 (如 buy_amt_usd) 误当美元。
   - 严格采用白盒物理准则: cost_usd (GMGN链上实付成本) 与 val_usd (当前持仓市值 hold_amount * price_usd) 双模态取大。
3. 统计代币维度的资金穿透总量、大户户数、持仓占比与白盒明细。
4. 绝无代币硬编码与过拟合。
"""
from __future__ import annotations
import sqlite3
import logging
from typing import Dict, List, Any

logger = logging.getLogger("meta-verdict.hop2_whale")


def scan_hop2_whales(
    src_conn: sqlite3.Connection,
    tokens: List[Dict[str, Any]],
    min_usd: float = 10000.0,
) -> Dict[str, Dict[str, Any]]:
    """
    扫描代币列表中存在 Hop-2 大额资金穿透的标的
    
    参数:
        src_conn: /opt/select-coin/data/select.db 连接
        tokens: 代币列表 [{"chain": ..., "token_address": ..., "token_symbol": ...}]
        min_usd: 单户有效真金门槛 (默认 $10,000.0)
    
    返回:
        以 "{chain}_{token_address.lower()}" 为 key 的汇总字典
    """
    result = {}
    if not tokens:
        return result

    cursor = src_conn.cursor()

    for t in tokens:
        chain = t.get("chain", "")
        addr = t.get("token_address", "").lower()
        sym = t.get("token_symbol", "")
        key = f"{chain}_{addr}"

        # 1. 查询该代币最新价格
        try:
            p_row = cursor.execute(
                "SELECT price_usd FROM gecko_market_data WHERE token_address = ? AND price_usd > 0 ORDER BY scan_time DESC LIMIT 1",
                (addr,)
            ).fetchone()
            price_usd = float(p_row[0]) if p_row and p_row[0] is not None else 0.0
        except Exception:
            price_usd = 0.0

        # 2. 查询最新快照时间
        try:
            snap_row = cursor.execute(
                "SELECT MAX(snapshot_time) FROM bubblemap_holders WHERE chain = ? AND token_address = ?",
                (chain, addr)
            ).fetchone()
            latest_snap = snap_row[0] if snap_row else None
        except Exception as e:
            logger.debug(f"查询快照时间失败 {sym} ({addr}): {e}")
            latest_snap = None

        if not latest_snap:
            continue

        # 3. 物理查询最新快照中二跳推断大户 (dex_ratio_hop2 >= 0.5 且 非CEX 非合约)
        try:
            h_rows = cursor.execute("""
                SELECT rank, wallet_address, hold_amount, hold_percentage, buy_amt_usd,
                       dex_ratio, dex_ratio_hop2, gmgn_verified, gmgn_buy_cnt, gmgn_buy_cost_usd,
                       acc_score, is_accumulating
                FROM bubblemap_holders
                WHERE chain = ? AND token_address = ? AND snapshot_time = ?
                  AND is_cex = 0 AND is_contract = 0
                  AND (dex_ratio_hop2 IS NOT NULL AND dex_ratio_hop2 >= 0.5)
                ORDER BY hold_percentage DESC
            """, (chain, addr, latest_snap)).fetchall()
        except Exception as e:
            logger.debug(f"查询 Hop-2 大户失败 {sym} ({addr}): {e}")
            continue

        whales = []
        tot_usd = 0.0
        tot_pct = 0.0
        max_u = 0.0

        for hr in h_rows:
            r_rank, r_addr, r_amt, r_pct, r_buy_usd, r_dex, r_hop2, r_gmgn, r_buy_cnt, r_gmgn_cost, r_score, r_acc = hr
            
            # 白盒物理核算：GMGN买入实付美元成本 vs 当前持仓市值
            cost_usd = float(r_gmgn_cost or 0.0)
            hold_amt = float(r_amt or 0.0)
            val_usd = hold_amt * price_usd if price_usd > 0 else 0.0
            eff_usd = max(cost_usd, val_usd)

            if eff_usd >= min_usd:
                whales.append({
                    "rank": r_rank,
                    "address": r_addr,
                    "hold_amount": hold_amt,
                    "hold_pct": float(r_pct or 0.0),
                    "cost_usd": cost_usd,
                    "val_usd": val_usd,
                    "effective_usd": eff_usd,
                    "hop2_ratio": float(r_hop2 or 0.0),
                    "dex_ratio": float(r_dex or 0.0),
                    "gmgn_verified": r_gmgn,
                    "gmgn_buy_cnt": r_buy_cnt or 0,
                    "is_accumulating": bool(r_acc)
                })
                tot_usd += eff_usd
                tot_pct += float(r_pct or 0.0)
                if eff_usd > max_u:
                    max_u = eff_usd

        if whales:
            result[key] = {
                "chain": chain,
                "token_address": addr,
                "token_symbol": sym,
                "price_usd": price_usd,
                "latest_snapshot": latest_snap,
                "whales_count": len(whales),
                "total_usd": tot_usd,
                "total_pct": tot_pct,
                "max_usd": max_u,
                "whales": whales,
            }

    return result
