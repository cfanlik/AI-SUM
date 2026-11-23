#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
leverage_crash_radar — 全网高杠杆衍生品去杠杆与闪崩踩踏风控雷达引擎 (Screen 11 Core)
实现四态有限状态机 (FSM) 与 CEX 主导暴涨逼空、时序顶背离、连环踩踏去杠杆三维漏斗判定。
"""
from __future__ import annotations

import os
import sys
import sqlite3
import logging
from datetime import datetime

logger = logging.getLogger("leverage_crash_radar")

def get_db_connections(sum_db_path: str = None, src_db_path: str = None):
    if not sum_db_path:
        sum_db_path = os.environ.get("SUM_DB_PATH", "/opt/AI-SUM/select-sum.db")
    if not src_db_path:
        src_db_path = os.environ.get("SRC_DB_PATH", "/opt/select-coin/data/select.db")
    
    conn = sqlite3.connect(sum_db_path)
    conn.row_factory = sqlite3.Row
    # 动态挂载 select.db 作为 src_db
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
    """
    根据四态状态机评定标的当前所处的杠杆与风控生命周期阶段
    """
    oi_mcap_ratio = (total_oi_usd / market_cap_usd) if market_cap_usd > 0 else 0.0
    
    # 脆性系数 (BidDepth / OI)，若深度未知兜底为极脆弱
    fragility_ratio = (bid_depth_2pct / total_oi_usd) if (bid_depth_2pct > 0 and total_oi_usd > 0) else 0.0
    
    stage = "─ DORMANT"
    verdict = "常规蓄水观察"
    strategy = "正常持有/观察"
    risk_level = "GREEN"
    is_squeeze = False
    is_divergence = False
    is_crash = False
    is_overheat = False
    
    # 1. 状态 3: 连环踩踏去杠杆态 (CASCADING_CRASH)
    if total_oi_usd >= 3000000 and (oi_change_24h <= -0.30 or (oi_change_24h <= -0.20 and price_change_24h <= -0.15)):
        stage = "💀 CASCADING-CRASH"
        verdict = f"单日去杠杆 {oi_change_24h*100:.1f}%，多杀多连环踩踏中"
        strategy = "🛑 严禁抄底接飞刀 / 物理阻断做多"
        risk_level = "BLACK"
        is_crash = True
        
    # 2. 状态 2: 极度脆弱火药桶态 (FRAGILITY_OVERHEAT)
    elif oi_mcap_ratio >= 2.0 or (total_oi_usd >= 10000000 and oi_mcap_ratio >= 1.0):
        is_overheat = True
        # 顶背离检测：价格滞涨/微涨，但 OI 剧烈流失
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
            
    # 3. 状态 1: 高杠杆轧空扩张态 (SQUEEZE_EXPANSION)
    elif total_oi_usd >= 5000000 and (oi_change_24h >= 0.80 or (oi_change_24h >= 0.35 and long_short_ratio <= 0.70)):
        stage = "🚀 SQUEEZE-EXPANSION"
        if long_short_ratio <= 0.60:
            verdict = f"OI激增 +{oi_change_24h*100:.1f}%，空头极度密集 (LSR {long_short_ratio:.2f}) 强逼空"
        else:
            verdict = f"OI单日翻倍扩张 (+{oi_change_24h*100:.1f}%)，多头资金强力推升"
        strategy = "⚡ 顺势紧跟多头 / 设定动态移动止盈"
        risk_level = "PURPLE"
        is_squeeze = True
        
    # 4. 状态 0: 潜伏蓄水态
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

def run_leverage_crash_scan(sum_db_path: str = None, src_db_path: str = None) -> dict:
    """
    全量扫描并输出第十一屏四大风控数据看板
    """
    conn = get_db_connections(sum_db_path, src_db_path)
    
    # 1. 尝试从 alpha_oi_daily_tracking 获取最新快照
    # 同时关联 src_db.futures_snapshots 与 src_db.tokens
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
        
    # 分解看板
    # 看板 A: 火药桶与高杠杆榜 (OI/Mcap 降序或 OI 规模排序)
    overheat_list = sorted([x for x in items if x["is_overheat"] or x["total_oi_usd"] >= 10000000], 
                           key=lambda x: (x["oi_mcap_ratio"] if x["oi_mcap_ratio"] < 99 else 99, x["total_oi_usd"]), 
                           reverse=True)
                           
    # 看板 B: 时序顶背离雷达 (价格滞涨但 OI 急跌，或 OI 24h 降幅 >= 15%)
    divergence_list = sorted([x for x in items if x["oi_change_24h"] <= -0.15 and x["total_oi_usd"] >= 2000000],
                            key=lambda x: x["oi_change_24h"])
                            
    # 看板 C: 闪崩连环踩踏去杠杆 (is_crash)
    crash_list = sorted([x for x in items if x["is_crash"]],
                        key=lambda x: x["oi_change_24h"])
                        
    # 看板 D: 衍生品暴涨逼空突破榜 (is_squeeze)
    squeeze_list = sorted([x for x in items if x["is_squeeze"]],
                         key=lambda x: x["oi_change_24h"], reverse=True)
                         
    return {
        "overheat_top": overheat_list[:15],
        "divergence_top": divergence_list[:15],
        "crash_top": crash_list[:15],
        "squeeze_top": squeeze_list[:15],
        "total_scanned": len(items)
    }

if __name__ == "__main__":
    res = run_leverage_crash_scan()
    print(f"扫描完成，总标的数: {res['total_scanned']}")
    print(f"高杠杆火药桶 Top 3: {[x['symbol'] for x in res['overheat_top'][:3]]}")
    print(f"闪崩踩踏 Top 3: {[x['symbol'] for x in res['crash_top'][:3]]}")
    print(f"轧空暴涨 Top 3: {[x['symbol'] for x in res['squeeze_top'][:3]]}")
