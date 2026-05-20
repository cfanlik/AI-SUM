#!/usr/bin/env python3
"""
AI-SUM CEX-DEX 协同共振分析引擎 (C10 信号)
- 计算公式：
  C10 触发条件 = (24h CEX OI 变化 >= 30%) AND (链上吸筹大户的资金大比例来自 CEX 提现，即 dex_ratio_hop2 <= 0.30)
- 功能：捕获由 CEX 驱动暴涨的代币，生成 C10(CEX-DEX-ACC) 信号，在时效雷达报中高亮呈现。
"""
from __future__ import annotations

import os
import sys
import sqlite3
import argparse
from datetime import datetime, timezone

# ── 路径与配置 ──
_ENV_PATH = "/opt/AI-SUM/.env"
def _read_env(key: str, default: str = "") -> str:
    try:
        for line in open(_ENV_PATH):
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return default

SUM_DB_PATH = _read_env("SUM_DB_PATH", "/opt/AI-SUM/select-sum.db")
SRC_DB_PATH = _read_env("SRC_DB_PATH", "/opt/select-coin/data/select.db")

def get_direct_connection() -> sqlite3.Connection:
    """获取带只读 ATTACH 的 SQLite 连接"""
    conn = sqlite3.connect(SUM_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(f"ATTACH DATABASE '{SRC_DB_PATH}' AS src_db")
    return conn

def check_coordination(conn: sqlite3.Connection, chain: str, token_address: str) -> dict | None:
    """
    对指定代币执行 CEX-DEX 协同共振分析。
    若触发 C10(CEX-DEX-ACC) 信号，返回包含详细指标的字典，否则返回 None。
    """
    # 1. 检查 CEX 合约资金异动 (futures_snapshots)
    latest_ft = conn.execute("""
        SELECT scan_time, symbol, open_interest, oi_value_usd, oi_change_24h, funding_rate, long_short_ratio
        FROM src_db.futures_snapshots
        WHERE token_address = ?
        ORDER BY scan_time DESC LIMIT 1
    """, [token_address]).fetchone()

    if not latest_ft:
        return None

    oi_chg = latest_ft["oi_change_24h"] or 0.0
    oi_usd = latest_ft["oi_value_usd"] or 0.0
    symbol = latest_ft["symbol"] or "?"

    # 触发阈值：24h OI 暴增 >= 30%，且合约持仓有一定底色 (>= $50k)
    if oi_chg < 0.30 or oi_usd < 50000:
        return None

    # 2. 检查链上吸筹大户 (bubblemap_holders) 中是否有 CEX 提现共振
    # 2.1 找出最新一次的快照时间
    latest_snap = conn.execute("""
        SELECT MAX(snapshot_time) FROM src_db.bubblemap_holders
        WHERE chain = ? AND token_address = ?
    """, [chain, token_address]).fetchone()

    if not latest_snap or not latest_snap[0]:
        return None
    snap_time = latest_snap[0]

    # 2.2 提取该快照下 is_accumulating = 1 且资金来源主要来自 CEX 的非合约、非交易所大户
    # dex_ratio_hop2 <= 0.30 意味着超过 70% 的资金二跳来源为非 DEX（即 CEX 提现流入）
    whales = conn.execute("""
        SELECT wallet_address, hold_percentage, buy_amt_usd, dex_ratio_hop2, acc_score
        FROM src_db.bubblemap_holders
        WHERE chain = ? AND token_address = ? AND snapshot_time = ?
          AND is_accumulating = 1 AND is_cex = 0 AND is_contract = 0
    """, [chain, token_address, snap_time]).fetchall()

    if not whales:
        return None

    cex_withdraw_whales = []
    total_acc_whales = len(whales)

    for w in whales:
        dex_hop2 = w["dex_ratio_hop2"]
        # 二跳非 DEX 占比高，或直接标记为 CEX 提现归集
        if dex_hop2 is not None and dex_hop2 <= 0.30:
            cex_withdraw_whales.append(w)

    if not cex_withdraw_whales:
        return None

    # 协同共振达成！
    cex_withdraw_whales_cnt = len(cex_withdraw_whales)
    cex_withdraw_hold_pct = sum(w["hold_percentage"] for w in cex_withdraw_whales)
    avg_acc_score = sum(w["acc_score"] for w in cex_withdraw_whales) / cex_withdraw_whales_cnt

    return {
        "triggered": True,
        "signal": "C10(CEX-DEX-ACC)",
        "symbol": symbol,
        "oi_change_24h": oi_chg,
        "oi_value_usd": oi_usd,
        "funding_rate": latest_ft["funding_rate"] or 0.0,
        "long_short_ratio": latest_ft["long_short_ratio"] or 1.0,
        "cex_withdraw_whales_cnt": cex_withdraw_whales_cnt,
        "cex_withdraw_hold_pct": round(cex_withdraw_hold_pct, 3),
        "avg_acc_score": round(avg_acc_score, 1),
        "total_acc_whales": total_acc_whales,
        "snap_time": snap_time
    }

def scan_all_coordination(conn: sqlite3.Connection) -> list[dict]:
    """遍历符合 CEX 合约异动条件的代币进行 CEX-DEX 协同共振检测（漏斗级超高效率优化版）"""
    # 1. 找出最新一期 futures_snapshots 扫描时间
    latest_scan_time_row = conn.execute("""
        SELECT MAX(scan_time) FROM src_db.futures_snapshots
    """).fetchone()
    if not latest_scan_time_row or not latest_scan_time_row[0]:
        return []
    latest_scan_time = latest_scan_time_row[0]

    # 2. 从符合 CEX 暴增阈值的代币列表中进行筛选
    # 限制 24h OI 变化 >= 30%，且 OI 总额 >= $50k，且只针对最新扫描数据
    candidates = conn.execute("""
        SELECT DISTINCT token_address, symbol, oi_change_24h, oi_value_usd
        FROM src_db.futures_snapshots
        WHERE scan_time = ? AND oi_change_24h >= 0.30 AND oi_value_usd >= 50000 AND token_address IS NOT NULL
    """, [latest_scan_time]).fetchall()

    results = []
    for c in candidates:
        token_address = c["token_address"]
        # 在 bubblemap_holders 中找匹配的链
        chain_row = conn.execute("""
            SELECT chain FROM src_db.bubblemap_holders
            WHERE token_address = ? LIMIT 1
        """, [token_address]).fetchone()
        
        chain = chain_row["chain"] if chain_row else "bsc"
        
        res = check_coordination(conn, chain, token_address)
        if res:
            res["chain"] = chain
            res["token_address"] = token_address
            results.append(res)
    return results

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CEX-DEX Coordination Engine")
    parser.add_argument("--test", action="store_true", help="Run diagnostic test over all tokens")
    parser.add_argument("--address", type=str, help="Test a single token address")
    parser.add_argument("--chain", type=str, default="bsc", help="Chain of single token (default: bsc)")
    args = parser.parse_args()

    conn = get_direct_connection()

    if args.address:
        print(f"[*] 正在测试单代币: {args.chain}/{args.address}...")
        res = check_coordination(conn, args.chain, args.address)
        if res:
            print(f"[C10 TRIGGERED] 触发协同共振！")
            for k, v in res.items():
                print(f"  {k}: {v}")
        else:
            print("[-] 未触发协同共振。")
    elif args.test:
        print("[*] 正在执行漏斗加速版全库协同共振检测诊断...")
        matched = scan_all_coordination(conn)
        print(f"\n[+] 诊断完成，共捕获到 {len(matched)} 个符合 C10 协同共振信号的代币：")
        print("-" * 80)
        for m in matched:
            print(f"代币: {m['symbol']} ({m['chain']}:{m['token_address']})")
            print(f"  CEX 合约: OI 24h变化: +{m['oi_change_24h']*100:.1f}%, OI总额: ${m['oi_value_usd']:,.0f}")
            print(f"  链上共振: CEX提现吸筹大户: {m['cex_withdraw_whales_cnt']}/{m['total_acc_whales']} 个, "
                  f"提现大户持仓: {m['cex_withdraw_hold_pct']}%")
            print("-" * 80)
    else:
        # 默认模式，直接全库检测并输出简短摘要
        matched = scan_all_coordination(conn)
        print(f"CEX-DEX 协同共振分析就绪。当前共鸣代币数: {len(matched)}")
    
    conn.close()
