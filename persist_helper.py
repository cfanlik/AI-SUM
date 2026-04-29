#!/usr/bin/env python3
"""
在 VPS 上注入持久化代码到 opus/whale/unified 三个引擎的 run.py
在 save_md_leaderboard / save_md_radar / run_full_scan 之后插入 save_to_sumdb 调用
"""
import sqlite3
import sys
import os

def _read_sum_db() -> str:
    env = "/opt/AI-SUM/.env"
    try:
        for line in open(env):
            if line.startswith("SUM_DB_PATH="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return "/opt/AI-SUM/select-sum.db"

SUM_DB = _read_sum_db()
SCAN_TIME = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_tables():
    conn = sqlite3.connect(SUM_DB)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS opus_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time TEXT NOT NULL,
            chain TEXT NOT NULL,
            token_address TEXT NOT NULL,
            token_symbol TEXT,
            acc_confidence REAL DEFAULT 0,
            dist_confidence REAL DEFAULT 0,
            verdict TEXT DEFAULT 'NEUTRAL',
            acc_cnt INTEGER DEFAULT 0,
            dex_verified_pct REAL DEFAULT 0,
            cex_delta_pct REAL DEFAULT 0,
            phase TEXT DEFAULT '',
            lp_usd REAL DEFAULT 0,
            vl_ratio REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS whale_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time TEXT NOT NULL,
            chain TEXT NOT NULL,
            token_address TEXT NOT NULL,
            token_symbol TEXT,
            confidence REAL DEFAULT 0,
            level TEXT DEFAULT 'CLEAN',
            top2_hold REAL DEFAULT 0,
            top5_hold REAL DEFAULT 0,
            lp_usd REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS unified_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time TEXT NOT NULL,
            chain TEXT NOT NULL,
            token_address TEXT NOT NULL,
            token_symbol TEXT,
            signal_level TEXT DEFAULT '',
            score REAL DEFAULT 0,
            verdict TEXT DEFAULT 'NEUTRAL',
            acc_cnt INTEGER DEFAULT 0,
            lp_usd REAL DEFAULT 0
        );
    """)
    conn.commit()
    conn.close()
    print("[ok] 三个持久化表已就绪")


def save_opus(results):
    """opus-scan VerdictResult 列表存入 opus_snapshots"""
    conn = sqlite3.connect(SUM_DB)
    rows = []
    for r in results:
        if r.verdict in ("NEUTRAL",):
            continue
        rows.append((
            SCAN_TIME, r.chain, r.token_address, r.symbol,
            r.acc_confidence, r.dist_confidence, r.verdict,
            r.acc_cnt, r.dex_verified_pct, r.cex_delta_pct,
            r.phase or "",
            r.lp_usd or 0, r.vl_ratio or 0,
        ))
    if rows:
        conn.executemany("""
            INSERT INTO opus_snapshots
            (scan_time,chain,token_address,token_symbol,acc_confidence,dist_confidence,
             verdict,acc_cnt,dex_verified_pct,cex_delta_pct,phase,lp_usd,vl_ratio)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows)
        conn.commit()
        print(f"[opus] 已保存 {len(rows)} 条结果到 select-sum.db")
    conn.close()


def save_whale(results):
    """whale-scan WhaleVerdict 列表存入 whale_snapshots"""
    conn = sqlite3.connect(SUM_DB)
    rows = []
    for r in results:
        if r.level == "CLEAN":
            continue
        cp = r.concentration
        rows.append((
            SCAN_TIME, r.chain, r.token_address, r.symbol,
            r.confidence, r.level,
            cp.top2_hold if cp else 0,
            cp.top5_hold if cp else 0,
            r.lp_usd or 0,
        ))
    if rows:
        conn.executemany("""
            INSERT INTO whale_snapshots
            (scan_time,chain,token_address,token_symbol,confidence,level,
             top2_hold,top5_hold,lp_usd)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, rows)
        conn.commit()
        print(f"[whale] 已保存 {len(rows)} 条结果到 select-sum.db")
    conn.close()


def save_unified(results):
    """unified-scan 结果列表存入 unified_snapshots"""
    conn = sqlite3.connect(SUM_DB)
    rows = []
    for r in results:
        sig = getattr(r, "signal_level", "") or getattr(r, "verdict", "")
        if not sig or sig == "NEUTRAL":
            continue
        rows.append((
            SCAN_TIME,
            getattr(r, "chain", "bsc"),
            getattr(r, "token_address", ""),
            getattr(r, "symbol", getattr(r, "token_symbol", "?")),
            sig,
            getattr(r, "score", 0) or 0,
            getattr(r, "verdict", sig),
            getattr(r, "acc_cnt", 0) or 0,
            getattr(r, "lp_usd", 0) or 0,
        ))
    if rows:
        conn.executemany("""
            INSERT INTO unified_snapshots
            (scan_time,chain,token_address,token_symbol,signal_level,score,verdict,acc_cnt,lp_usd)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, rows)
        conn.commit()
        print(f"[unified] 已保存 {len(rows)} 条结果到 select-sum.db")
    conn.close()


if __name__ == "__main__":
    ensure_tables()
    print("persist_helper.py 就绪，供各引擎 import 调用")
