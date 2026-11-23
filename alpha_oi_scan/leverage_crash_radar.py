#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
leverage_crash_radar — 全网高杠杆衍生品去杠杆与闪崩踩踏风控雷达引擎 (Screen 11 Core)
包含：四态有限状态机 (FSM)、暴涨逼空/顶背离/踩踏判定、以及连续 5 轮多快照时序演进轨迹 (σ) 拟合矩阵。
"""
from __future__ import annotations

import os
import sys
import sqlite3
import logging
import math
from datetime import datetime

logger = logging.getLogger("leverage_crash_radar")

def get_db_connections(sum_db_path: str = None, src_db_path: str = None):
    if not sum_db_path:
        sum_db_path = os.environ.get("SUM_DB_PATH", "/opt/AI-SUM/select-sum.db")
    if not src_db_path:
        src_db_path = os.environ.get("SRC_DB_PATH", "/opt/select-coin/data/select.db")
    
    conn = sqlite3.connect(sum_db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"ATTACH DATABASE '{src_db_path}' AS src_db")
    except sqlite3.OperationalError:
        pass
    return conn

def classify_state_and_risk(
    symbol: str,
    total_oi_usd: float,
    market_cap_usd: float,
    oi_change_24h: float,
    oi_change_1h: float,
    funding_rate: float,
    long_short_ratio: float,
    mark_price: float,
    price_change_24h: float = 0.0,
    bid_depth_2pct: float = 0.0
) -> dict:
    oi_mcap_ratio = (total_oi_usd / market_cap_usd) if market_cap_usd > 0 else 0.0
    fragility_ratio = (bid_depth_2pct / total_oi_usd) if (bid_depth_2pct > 0 and total_oi_usd > 0) else 0.0
    
    stage = "─ DORMANT"
    verdict = "常规蓄水观察"
    strategy = "正常持有/观察"
    risk_level = "GREEN"
    is_squeeze = False
    is_divergence = False
    is_crash = False
    is_overheat = False
    
    if total_oi_usd >= 3000000 and (oi_change_24h <= -0.30 or (oi_change_24h <= -0.20 and price_change_24h <= -0.15)):
        stage = "💀 CASCADING-CRASH"
        verdict = f"单日去杠杆 {oi_change_24h*100:.1f}%，多杀多连环踩踏中"
        strategy = "🛑 严禁抄底接飞刀 / 物理阻断做多"
        risk_level = "BLACK"
        is_crash = True
        
    elif oi_mcap_ratio >= 2.0 or (total_oi_usd >= 10000000 and oi_mcap_ratio >= 1.0):
        is_overheat = True
        if price_change_24h >= -0.05 and oi_change_24h <= -0.15:
            stage = "🚨 FRAGILITY-DIVERGENCE"
            verdict = f"杠杆倍率 {oi_mcap_ratio:.1f}x 且出现顶背离 (ΔOI {oi_change_24h*100:.1f}%)"
            strategy = "🚨 极度高危 / 主力撤离 / 现货分批清仓"
            risk_level = "RED"
            is_divergence = True
        else:
            stage = "⚠️ FRAGILITY-OVERHEAT"
            verdict = f"OI/Mcap 达 {oi_mcap_ratio*100:.0f}%，衍生品极端拥挤火药桶"
            strategy = "⚠️ 警惕现货抽单闪崩 / 逢高止盈减仓"
            risk_level = "RED"
            
    elif total_oi_usd >= 5000000 and (oi_change_24h >= 0.80 or (oi_change_24h >= 0.35 and long_short_ratio <= 0.70)):
        stage = "🚀 SQUEEZE-EXPANSION"
        if long_short_ratio <= 0.60:
            verdict = f"OI激增 +{oi_change_24h*100:.1f}%，空头极度密集 (LSR {long_short_ratio:.2f}) 强逼空"
        else:
            verdict = f"OI单日翻倍扩张 (+{oi_change_24h*100:.1f}%)，多头资金强力推升"
        strategy = "⚡ 顺势紧跟多头 / 设定动态移动止盈"
        risk_level = "PURPLE"
        is_squeeze = True
        
    else:
        stage = "🌱 DORMANT-SETUP"
        verdict = f"OI规模稳步运行，未见极端杠杆失衡"
        strategy = "👀 常规吸筹跟踪"
        risk_level = "GREEN"

    return {
        "symbol": symbol,
        "total_oi_usd": total_oi_usd,
        "market_cap_usd": market_cap_usd,
        "oi_mcap_ratio": oi_mcap_ratio,
        "oi_change_24h": oi_change_24h,
        "oi_change_1h": oi_change_1h,
        "funding_rate": funding_rate,
        "long_short_ratio": long_short_ratio,
        "mark_price": mark_price,
        "price_change_24h": price_change_24h,
        "bid_depth_2pct": bid_depth_2pct,
        "fragility_ratio": fragility_ratio,
        "stage": stage,
        "decision_verdict": verdict,
        "strategy": strategy,
        "risk_level": risk_level,
        "is_squeeze": is_squeeze,
        "is_divergence": is_divergence,
        "is_crash": is_crash,
        "is_overheat": is_overheat
    }

def get_5_snapshot_trajectories(conn: sqlite3.Connection, symbols: list[str]) -> list[dict]:
    if not symbols:
        return []
        
    trajectories = []
    for sym in symbols:
        try:
            cur = conn.execute("""
                SELECT oi_value_usd, mark_price, funding_rate, long_short_ratio, scan_time
                FROM src_db.futures_snapshots
                WHERE symbol = ?
                ORDER BY scan_time DESC
                LIMIT 5
            """, (sym,))
            rows = [dict(r) for r in cur.fetchall()]
            if len(rows) < 3:
                continue
            rows.reverse()
            
            ois = [r['oi_value_usd'] / 1e6 for r in rows if r['oi_value_usd']]
            if not ois:
                continue
                
            init_oi, curr_oi = ois[0], ois[-1]
            delta_oi = curr_oi - init_oi
            pct_oi = (delta_oi / init_oi * 100) if init_oi > 0 else 0.0
            
            mean_oi = sum(ois) / len(ois)
            variance = sum((x - mean_oi) ** 2 for x in ois) / len(ois)
            std_oi = math.sqrt(variance)
            sigma_norm = (std_oi / mean_oi) if mean_oi > 0 else 0.0
            
            traj_str = " → ".join([f"${x:.1f}M" for x in ois])
            
            if pct_oi >= 40.0 and sigma_norm >= 0.15:
                thesis = "🚀 杠杆强力扩张逼空 (加速拉升)"
            elif pct_oi <= -30.0:
                thesis = "💀 连续去杠杆踩踏中 (闪崩避险)"
            elif pct_oi <= -15.0 and std_oi >= 1.0:
                thesis = "🚨 高位持续拐头流失 (顶背离出货)"
            elif sigma_norm >= 0.30:
                thesis = "⚡ 多空激烈拉锯博弈 (洗盘剧烈)"
            else:
                thesis = "🌱 稳步沉淀蓄水 (低波动运行)"
                
            trajectories.append({
                "symbol": sym,
                "trajectory_str": traj_str,
                "init_oi": init_oi,
                "curr_oi": curr_oi,
                "delta_oi": delta_oi,
                "pct_oi": pct_oi,
                "std_oi": std_oi,
                "sigma_norm": sigma_norm,
                "thesis": thesis
            })
        except Exception as e:
            logger.debug(f"提取 {sym} 5轮轨迹失败: {e}")
            continue
            
    trajectories.sort(key=lambda x: abs(x["pct_oi"]), reverse=True)
    return trajectories

def run_leverage_crash_scan(sum_db_path: str = None, src_db_path: str = None) -> dict:
    conn = get_db_connections(sum_db_path, src_db_path)
    
    try:
        cur = conn.execute("""
            SELECT 
                f.symbol,
                f.oi_value_usd as total_oi_usd,
                COALESCE(a.market_cap_usd, 0.0) as market_cap_usd,
                f.oi_change_24h,
                f.oi_change_1h,
                f.funding_rate,
                f.long_short_ratio,
                f.mark_price,
                f.scan_time
            FROM src_db.futures_snapshots f
            LEFT JOIN (
                SELECT symbol, market_cap_usd 
                FROM alpha_oi_daily_tracking 
                GROUP BY symbol 
                HAVING id = MAX(id)
            ) a ON f.symbol = a.symbol
            WHERE f.scan_time = (SELECT MAX(scan_time) FROM src_db.futures_snapshots)
        """)
        rows = cur.fetchall()
    except Exception as e:
        logger.error(f"查询 futures_snapshots 失败: {e}")
        rows = []
        
    items = []
    for r in rows:
        row_dict = dict(r)
        classified = classify_state_and_risk(
            symbol=row_dict["symbol"],
            total_oi_usd=row_dict["total_oi_usd"] or 0.0,
            market_cap_usd=row_dict["market_cap_usd"] or 0.0,
            oi_change_24h=row_dict["oi_change_24h"] or 0.0,
            oi_change_1h=row_dict["oi_change_1h"] or 0.0,
            funding_rate=row_dict["funding_rate"] or 0.0,
            long_short_ratio=row_dict["long_short_ratio"] or 1.0,
            mark_price=row_dict["mark_price"] or 0.0
        )
        items.append(classified)
        
    overheat_list = sorted([x for x in items if x["is_overheat"] or x["total_oi_usd"] >= 10000000], 
                           key=lambda x: (x["oi_mcap_ratio"] if x["oi_mcap_ratio"] < 99 else 99, x["total_oi_usd"]), 
                           reverse=True)
                           
    divergence_list = sorted([x for x in items if x["oi_change_24h"] <= -0.15 and x["total_oi_usd"] >= 2000000],
                            key=lambda x: x["oi_change_24h"])
                            
    crash_list = sorted([x for x in items if x["is_crash"]],
                        key=lambda x: x["oi_change_24h"])
                        
    squeeze_list = sorted([x for x in items if x["is_squeeze"]],
                         key=lambda x: x["oi_change_24h"], reverse=True)
                         
    key_symbols = list(dict.fromkeys(
        [x["symbol"] for x in overheat_list[:10]] + 
        [x["symbol"] for x in crash_list[:5]] + 
        [x["symbol"] for x in squeeze_list[:10]] + 
        ['EVAA', 'AIN', 'Q', 'AKE', 'IRYS', 'JCT', 'BTW', 'FIL', 'CAKE']
    ))
    trajectory_list = get_5_snapshot_trajectories(conn, key_symbols)
                         
    return {
        "overheat_top": overheat_list[:15],
        "divergence_top": divergence_list[:15],
        "crash_top": crash_list[:15],
        "squeeze_top": squeeze_list[:15],
        "trajectory_top": trajectory_list[:15],
        "total_scanned": len(items)
    }

if __name__ == "__main__":
    res = run_leverage_crash_scan()
    print(f"扫描完成，总数: {res['total_scanned']}")
    print(f"5轮轨迹提取数量: {len(res['trajectory_top'])}")
    for t in res['trajectory_top'][:5]:
        print(f"  {t['symbol']}: {t['trajectory_str']} | Δ: {t['delta_oi']:+.2f}M ({t['pct_oi']:+.1f}%) | σ: {t['std_oi']:.2f}")
