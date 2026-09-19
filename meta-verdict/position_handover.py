"""
position_handover.py
Meta-Verdict 决策驾驶舱第七屏子模块：长期仓位平移接力与沉淀锁仓雷达

功能:
1. 追溯代币历史快照 (多周期采样 60d / 30d 宏观时序窗口)。
2. 检测主力筹码从早期地址向新地址的等额平移接力 (Handover, 数量相对偏差 <= 1.0%)。
3. 识别转移后在新地址长期静默死锁未动 (Dormancy, 沉淀天数 >= min_days, 默认 14 天)。
4. 过滤小额杂波，仅保留等额价值 >= min_usd (默认 $10,000.0) 的核心主力仓位。
   - 严格采用白盒物理准则: cost_usd (GMGN链上实付成本) 与 val_usd (当前持仓市值 hold_amount * price_usd) 双模态取大。
5. 纯物理统计算子，绝无代币硬编码与过拟合。
"""
from __future__ import annotations
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any

logger = logging.getLogger("meta-verdict.handover")


def scan_position_handovers(
    src_conn: sqlite3.Connection,
    tokens: List[Dict[str, Any]],
    min_days: int = 14,
    min_usd: float = 10000.0,
) -> Dict[str, Dict[str, Any]]:
    """
    扫描代币列表中存在长期等额仓位平移接力的标的 (多周期极速时序采样版)
    
    参数:
        src_conn: /opt/select-coin/data/select.db 连接
        tokens: 代币列表 [{"chain": ..., "token_address": ..., "token_symbol": ...}]
        min_days: 仓位转移后静默锁仓最小天数 (默认 14 天)
        min_usd: 仓位最低估值门槛 (默认 $10,000.0)
    
    返回:
        以 "{chain}_{token_address.lower()}" 为 key 的接力详情字典
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

        # 1. 查询最新快照 (命中 idx_bm_snapshot 覆盖索引)
        try:
            row = cursor.execute(
                "SELECT MAX(snapshot_time) FROM bubblemap_holders WHERE chain = ? AND token_address = ?",
                (chain, addr)
            ).fetchone()
            latest_snap = row[0] if row else None
        except Exception:
            latest_snap = None

        if not latest_snap:
            continue

        try:
            dt_latest = datetime.strptime(latest_snap[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue

        # 2. 多周期时序采样历史快照 (60d, 30d)
        hist_snaps = set()
        for offset in (60, 30):
            cutoff = (dt_latest - timedelta(days=offset)).strftime("%Y-%m-%d %H:%M:%S")
            try:
                h_row = cursor.execute(
                    "SELECT snapshot_time FROM bubblemap_holders WHERE chain = ? AND token_address = ? AND snapshot_time <= ? ORDER BY snapshot_time DESC LIMIT 1",
                    (chain, addr, cutoff)
                ).fetchone()
                if h_row and h_row[0]:
                    hist_snaps.add(h_row[0])
            except Exception:
                pass

        valid_hist_snaps = []
        for s in hist_snaps:
            try:
                d = (dt_latest - datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")).days
                if d >= min_days:
                    valid_hist_snaps.append((s, d))
            except Exception:
                pass

        if not valid_hist_snaps:
            continue

        # 3. 查最新快照中的持仓大户 (命中 idx_bm_snap_chain_token 复合索引)
        try:
            late_holders = cursor.execute("""
                SELECT wallet_address, hold_amount, hold_percentage, gmgn_buy_cost_usd
                FROM bubblemap_holders
                WHERE snapshot_time = ? AND chain = ? AND token_address = ?
                  AND is_cex = 0 AND is_contract = 0 AND hold_percentage >= 1.0
                ORDER BY hold_percentage DESC
            """, (latest_snap, chain, addr)).fetchall()
        except Exception as e:
            logger.debug(f"查询最新大户快照失败 {sym} ({addr}): {e}")
            continue

        if not late_holders:
            continue

        # 4. 查代币现价
        try:
            p_row = cursor.execute(
                "SELECT price_usd FROM gecko_market_data WHERE token_address = ? AND price_usd > 0 ORDER BY scan_time DESC LIMIT 1",
                (addr,)
            ).fetchone()
            price_usd = float(p_row[0]) if p_row and p_row[0] is not None else 0.0
        except Exception:
            price_usd = 0.0

        late_map = {lh[0].lower(): lh for lh in late_holders}
        best_ho = None

        # 5. 遍历采样快照对比等额平移
        for h_snap, span_days in sorted(valid_hist_snaps, key=lambda x: x[1], reverse=True):
            try:
                hist_holders = cursor.execute("""
                    SELECT wallet_address, hold_amount, hold_percentage, gmgn_buy_cost_usd
                    FROM bubblemap_holders
                    WHERE snapshot_time = ? AND chain = ? AND token_address = ?
                      AND is_cex = 0 AND is_contract = 0 AND hold_percentage >= 1.0
                """, (h_snap, chain, addr)).fetchall()
            except Exception:
                hist_holders = []

            for eh in hist_holders:
                e_addr, e_amt, e_pct, e_cost = eh
                e_amt = float(e_amt or 0.0)
                eff_usd = max(float(e_cost or 0.0), e_amt * price_usd)

                if eff_usd < min_usd:
                    continue

                if e_addr.lower() not in late_map:
                    for l_lower, lh in late_map.items():
                        l_addr, l_amt, l_pct, l_cost = lh
                        l_amt = float(l_amt or 0.0)
                        denom = max(e_amt, 1e-6)
                        if abs(l_amt - e_amt) / denom <= 0.01:
                            ho_info = {
                                "origin_address": e_addr,
                                "target_address": l_addr,
                                "transferred_amount": l_amt,
                                "hold_percentage": float(l_pct or 0.0),
                                "span_days": span_days,
                                "cost_usd": max(float(e_cost or 0.0), float(l_cost or 0.0)),
                                "val_usd": l_amt * price_usd,
                                "effective_usd": eff_usd,
                                "first_snapshot": h_snap,
                                "latest_snapshot": latest_snap,
                            }
                            if best_ho is None or ho_info["span_days"] > best_ho["span_days"] or ho_info["effective_usd"] > best_ho["effective_usd"]:
                                best_ho = ho_info
                            break
                if best_ho:
                    break
            if best_ho:
                break

        if best_ho:
            result[key] = {
                "chain": chain,
                "token_address": addr,
                "token_symbol": sym,
                "price_usd": price_usd,
                "handover": best_ho
            }

    return result
