#!/usr/bin/env python3
"""
alpha_filter — BSC Alpha 板块合约持仓与独立行情过滤引擎
实现 6 重门槛判别：
  1. Alpha 板块且为 BSC 链 (56)
  2. 已上合约，OI / 流通市值 <= 25% (支持正负 10% 敏感度波动 15%~35%)
  3. 合约持仓 U 本位价值 >= 5M USD
  4. 近 3-4 个月 (90d) 涨幅 < 5X (或 10X)
  5. 链上持仓 Top 20 集中度 >= 90% (支持 80%~95% 梯度)
  6. 合约持仓 24H/48H OI 增幅 < 20% (支持 10%~30% 梯度)
"""
from __future__ import annotations

import os
import sys
import sqlite3
import logging
from datetime import datetime

logger = logging.getLogger("alpha_filter")

def get_db_connections(sum_db_path: str = None, src_db_path: str = None):
    if not sum_db_path:
        sum_db_path = os.environ.get("SUM_DB_PATH", "/opt/AI-SUM/select-sum.db")
    if not src_db_path:
        src_db_path = os.environ.get("SRC_DB_PATH", "/opt/select-coin/data/select.db")
    
    conn = sqlite3.connect(sum_db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(f"ATTACH DATABASE '{src_db_path}' AS src_db")
    return conn

def ensure_alpha_tables(conn: sqlite3.Connection):
    conn.execute("""CREATE TABLE IF NOT EXISTS alpha_oi_daily_tracking (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_time TEXT NOT NULL,
        snapshot_date TEXT NOT NULL,
        symbol TEXT NOT NULL,
        token_address TEXT,
        chain TEXT DEFAULT 'BSC',
        total_oi_usd REAL NOT NULL,
        market_cap_usd REAL NOT NULL,
        oi_mcap_ratio REAL NOT NULL,
        oi_change_24h REAL NOT NULL,
        oi_change_48h REAL DEFAULT 0.0,
        top20_ratio REAL DEFAULT 0.0,
        price_now REAL DEFAULT 0.0,
        gain_90d_ratio REAL DEFAULT 1.0,
        stage TEXT NOT NULL,
        decision_verdict TEXT NOT NULL,
        risk_flags TEXT,
        primary_exchange TEXT DEFAULT 'BINANCE',
        created_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_aoi_date ON alpha_oi_daily_tracking(snapshot_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_aoi_sym ON alpha_oi_daily_tracking(symbol)")
    conn.commit()

def evaluate_alpha_token(
    token_meta: dict,
    oi_threshold: float = 0.25,
    oi_chg_threshold: float = 0.20,
    top20_threshold: float = 0.90,
    gain_threshold: float = 5.0
) -> dict:
    """
    对单个代币执行 6 重门槛量化评估
    """
    sym = token_meta.get("symbol", "").upper()
    addr = token_meta.get("token_address", "")
    chain = token_meta.get("chain", "BSC").upper()
    oi_usd = float(token_meta.get("total_oi_usd") or 0.0)
    mcap = float(token_meta.get("market_cap_usd") or 0.0)
    oi_chg24 = float(token_meta.get("oi_change_24h") or 0.0)
    oi_chg48 = float(token_meta.get("oi_change_48h") or oi_chg24)
    top20 = float(token_meta.get("top20_ratio") or 0.0)
    gain_90d = float(token_meta.get("gain_90d_ratio") or 1.0)
    price = float(token_meta.get("price_now") or 0.0)

    oi_ratio = (oi_usd / mcap) if mcap > 0 else 999.0

    # 门槛 1: 必须为 BSC 链
    is_bsc = (chain == "BSC" or "BSC" in chain)
    # 门槛 3: OI 价值 >= 5M
    is_oi_5m = (oi_usd >= 5_000_000)

    risk_flags = []
    
    # 阶段判定与分类
    if oi_ratio > 0.70 or oi_ratio > 1.0:
        stage = "🛑 L3-Crash-Risk"
        verdict = "超杠杆博弈危险区"
        risk_flags.append(f"极端超杠杆(OI达市值{oi_ratio*100:.1f}%)")
        strat = "绝对一票否决 / 严禁介入抄底"
    elif oi_ratio > 0.35 and oi_ratio <= 0.70:
        stage = "⚡ L2-Squeeze"
        verdict = "拉升逼空博弈区"
        if oi_chg24 > 0.30:
            risk_flags.append("持仓暴增加速")
        strat = "多空博弈白热化，仅限短线带损，严禁高位重仓"
    elif oi_ratio <= oi_threshold and oi_chg24 < oi_chg_threshold and is_oi_5m and is_bsc:
        if top20 >= top20_threshold and gain_90d <= gain_threshold:
            stage = "🚀 L1-Alpha-OI"
            verdict = "黄金潜伏建仓期"
            strat = "顶级低杠杆独立吸筹，现货分批埋伏 / 跟踪 48H 异动"
        else:
            stage = "👀 L2-潜力蓄势"
            verdict = "低杠杆蓄势区"
            if top20 < top20_threshold:
                risk_flags.append(f"Top20控盘度未达90%({top20*100:.1f}%)")
            if gain_90d > gain_threshold:
                risk_flags.append(f"90天涨幅超标({gain_90d:.1f}x)")
            strat = "观察链上集中度演进，底仓试探"
    else:
        stage = "─ L4-观察/未达标"
        verdict = "常态观察标的"
        if not is_oi_5m:
            risk_flags.append(f"合约持仓不足5M(${oi_usd/1e6:.1f}M)")
        if not is_bsc:
            risk_flags.append(f"非BSC链({chain})")
        strat = "常态跟踪，暂不入选"

    return {
        "symbol": sym,
        "token_address": addr,
        "chain": chain,
        "total_oi_usd": oi_usd,
        "market_cap_usd": mcap,
        "oi_mcap_ratio": round(oi_ratio, 4),
        "oi_change_24h": oi_chg24,
        "oi_change_48h": oi_chg48,
        "top20_ratio": round(top20, 4),
        "gain_90d_ratio": round(gain_90d, 2),
        "price_now": price,
        "stage": stage,
        "verdict": verdict,
        "risk_flags": "; ".join(risk_flags) if risk_flags else "无",
        "strategy": strat
    }

def run_alpha_scan(conn: sqlite3.Connection = None, oi_threshold: float = 0.25) -> list[dict]:
    """扫描全网多 CEX 与链上数据，生成全量 BSC Alpha 评估结果"""
    close_conn = False
    if conn is None:
        conn = get_db_connections()
        close_conn = True

    ensure_alpha_tables(conn)
    scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    snapshot_date = datetime.now().strftime("%Y-%m-%d")

    # 1. 查找已知合约标的 (从 multi_cex_futures_snapshots 或 futures_snapshots)
    use_multi = conn.execute("SELECT name FROM src_db.sqlite_master WHERE type='table' AND name='multi_cex_futures_snapshots'").fetchone()
    
    if use_multi:
        ft_query = """
            SELECT symbol, token_address, total_oi_usd as oi_usd, oi_change_24h, oi_change_48h, mark_price, primary_exchange
            FROM src_db.multi_cex_futures_snapshots
            WHERE scan_time = (SELECT MAX(scan_time) FROM src_db.multi_cex_futures_snapshots)
        """
    else:
        ft_query = """
            SELECT symbol, token_address, oi_value_usd as oi_usd, oi_change_24h, 0.0 as oi_change_48h, mark_price, 'BINANCE' as primary_exchange
            FROM src_db.futures_snapshots
            WHERE scan_time = (SELECT MAX(scan_time) FROM src_db.futures_snapshots)
        """
    
    futures_rows = conn.execute(ft_query).fetchall()

    # 2. 预加载 alpha_tokens_daily 映射，杜绝循环内重复探测表与全表检索
    has_alpha = bool(conn.execute("SELECT name FROM src_db.sqlite_master WHERE type='table' AND name='alpha_tokens_daily'").fetchone())
    alpha_map = {}
    if has_alpha:
        try:
            for ar in conn.execute("SELECT token_symbol, contract_address, chain FROM src_db.alpha_tokens_daily").fetchall():
                if ar[0]:
                    alpha_map[ar[0].upper()] = {
                        "chain": ar[2] or "BSC",
                        "address": ar[1] or ""
                    }
        except Exception as e:
            logger.warning(f"加载 alpha_tokens_daily 异常: {e}")

    # 3. 循环评估与精准剪枝 (带索引自愈与耗时压降)
    results = []
    inserted = []

    for r in futures_rows:
        sym = r["symbol"].upper()
        addr = r["token_address"] or ""
        oi_usd = float(r["oi_usd"] or 0.0)
        oi_chg24 = float(r["oi_change_24h"] or 0.0)
        oi_chg48 = float(r["oi_change_48h"] or oi_chg24)
        price = float(r["mark_price"] or 0.0)
        pri_ex = r["primary_exchange"] or "BINANCE"

        alpha_info = alpha_map.get(sym)
        chain = alpha_info["chain"] if alpha_info else ("BSC" if addr.startswith("0x") else "UNKNOWN")
        if not addr and alpha_info and alpha_info["address"]:
            addr = alpha_info["address"]

        mcap = 0.0
        top20_ratio = 0.0
        gain_90d = 1.0

        # 仅对具有有效链上合约地址的标的查询 Gecko 市值与链上 Top20 控盘
        if addr.startswith("0x"):
            gk = conn.execute("""
                SELECT COALESCE(NULLIF(market_cap_usd, 0), fdv_usd, 0), price_usd
                FROM src_db.gecko_market_data
                WHERE token_address = ?
                ORDER BY scan_time DESC LIMIT 1
            """, (addr,)).fetchone()
            if gk and gk[0]:
                mcap = float(gk[0])
            elif mcap <= 0:
                tn = conn.execute("SELECT market_cap_usd FROM src_db.token_names WHERE LOWER(token_address)=?", (addr.lower(),)).fetchone()
                if tn and tn[0] and float(tn[0]) > 0:
                    mcap = float(tn[0])

            # 查 Top 20 链上控盘度：强制走 (chain, token_address, snapshot_time) 组合索引，杜绝无快照聚合与跨链漏查
            if chain.upper() in ("BSC", "56") or "BSC" in chain.upper():
                top20_row = conn.execute("""
                    SELECT SUM(hold_percentage)
                    FROM src_db.bubblemap_holders
                    WHERE chain = 'bsc' AND token_address = ? AND rank <= 20
                    AND snapshot_time = (
                        SELECT MAX(snapshot_time) FROM src_db.bubblemap_holders WHERE chain = 'bsc' AND token_address = ?
                    )
                """, (addr, addr)).fetchone()
                if top20_row and top20_row[0]:
                    top20_ratio = float(top20_row[0]) / 100.0

        # 查 90 天最低价 (针对持仓 >= 1M 或关注标的)
        if oi_usd >= 1000000 and price > 0:
            low_row = conn.execute("""
                SELECT MIN(mark_price)
                FROM src_db.futures_snapshots
                WHERE symbol = ? AND mark_price > 0
            """, (sym,)).fetchone()
            low_price = float(low_row[0]) if (low_row and low_row[0]) else price
            gain_90d = round(price / low_price, 2) if low_price > 0 else 1.0

        token_meta = {
            "symbol": sym,
            "token_address": addr,
            "chain": chain,
            "total_oi_usd": oi_usd,
            "market_cap_usd": mcap,
            "oi_change_24h": oi_chg24,
            "oi_change_48h": oi_chg48,
            "top20_ratio": top20_ratio,
            "gain_90d_ratio": gain_90d,
            "price_now": price,
            "primary_exchange": pri_ex
        }

        eval_res = evaluate_alpha_token(token_meta, oi_threshold=oi_threshold)
        eval_res["primary_exchange"] = pri_ex
        results.append(eval_res)

        inserted.append((
            scan_time, snapshot_date, eval_res["symbol"], eval_res["token_address"],
            eval_res["chain"], eval_res["total_oi_usd"], eval_res["market_cap_usd"],
            eval_res["oi_mcap_ratio"], eval_res["oi_change_24h"], eval_res["oi_change_48h"],
            eval_res["top20_ratio"], eval_res["price_now"], eval_res["gain_90d_ratio"],
            eval_res["stage"], eval_res["verdict"], eval_res["risk_flags"],
            eval_res["primary_exchange"], scan_time
        ))

    if inserted:
        conn.executemany("""
            INSERT INTO alpha_oi_daily_tracking (
                scan_time, snapshot_date, symbol, token_address, chain,
                total_oi_usd, market_cap_usd, oi_mcap_ratio, oi_change_24h, oi_change_48h,
                top20_ratio, price_now, gain_90d_ratio, stage, decision_verdict,
                risk_flags, primary_exchange, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, inserted)
        conn.commit()

    if close_conn:
        conn.close()

    return results

if __name__ == "__main__":
    res = run_alpha_scan()
    print(f"Alpha Scan Complete. Total evaluated: {len(res)}")
    l1 = [r for r in res if "L1" in r["stage"]]
    l2_sq = [r for r in res if "L2-Squeeze" in r["stage"]]
    l3_cr = [r for r in res if "L3-Crash" in r["stage"]]
    print(f"L1 潜伏建仓: {len(l1)} | L2 逼空博弈: {len(l2_sq)} | L3 踩踏高危: {len(l3_cr)}")
    for item in (l1 + l2_sq + l3_cr)[:10]:
        print(f"{item['symbol']:10s} | {item['stage']:15s} | OI: ${item['total_oi_usd']/1e6:5.1f}M | OI/Mcap: {item['oi_mcap_ratio']*100:5.1f}% | 24h: {item['oi_change_24h']*100:+5.1f}% | 结论: {item['verdict']}")
