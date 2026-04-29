"""
meta-verdict 数据收集器
从 select-sum.db 读取 5 个引擎的最新扫描结果
"""
from __future__ import annotations
import sqlite3
import logging
from dataclasses import dataclass, field
from typing import Optional
import config

logger = logging.getLogger(__name__)


@dataclass
class TokenEngineData:
    """单个代币的跨引擎数据"""
    chain: str
    token_address: str
    token_symbol: str = "?"

    # master-scan
    master_signal:  str   = ""   # DIAMOND / RED / YELLOW / ""
    master_pattern: str   = ""

    # opus-scan
    opus_acc_conf:  float = 0.0
    opus_dist_conf: float = 0.0
    opus_verdict:   str   = ""

    # unified-scan
    unified_signal: str   = ""
    unified_score:  float = 0.0

    # whale-scan
    whale_level:    str   = ""
    whale_conf:     float = 0.0

    # cost-basis-scan
    cb_verdict:     str   = ""
    cb_acc_pct:     float = 0.0
    cb_dist_pct:    float = 0.0
    cb_vwap:        float = 0.0
    cb_gecko_price: float = 0.0
    cb_windfall_pct:float = 0.0
    cb_signals:     str   = ""

    # 计算结果
    meta_score:     float = 0.0
    engine_hits:    int   = 0     # 有数据的引擎数


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(config.SUM_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tables(conn: sqlite3.Connection):
    """确保所有引擎的持久化表存在"""
    conn.executescript("""
        -- opus-scan 结果表
        CREATE TABLE IF NOT EXISTS opus_snapshots (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time      TEXT NOT NULL,
            chain          TEXT NOT NULL,
            token_address  TEXT NOT NULL,
            token_symbol   TEXT,
            acc_confidence REAL DEFAULT 0,
            dist_confidence REAL DEFAULT 0,
            verdict        TEXT DEFAULT 'NEUTRAL',
            acc_cnt        INTEGER DEFAULT 0,
            dex_verified_pct REAL DEFAULT 0,
            cex_delta_pct  REAL DEFAULT 0,
            phase          TEXT DEFAULT '',
            lp_usd         REAL DEFAULT 0,
            vl_ratio       REAL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_opus_token ON opus_snapshots(chain, token_address, scan_time);

        -- unified-scan 结果表
        CREATE TABLE IF NOT EXISTS unified_snapshots (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time      TEXT NOT NULL,
            chain          TEXT NOT NULL,
            token_address  TEXT NOT NULL,
            token_symbol   TEXT,
            signal_level   TEXT DEFAULT '',
            score          REAL DEFAULT 0,
            verdict        TEXT DEFAULT 'NEUTRAL',
            acc_cnt        INTEGER DEFAULT 0,
            lp_usd         REAL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_unified_token ON unified_snapshots(chain, token_address, scan_time);

        -- whale-scan 结果表
        CREATE TABLE IF NOT EXISTS whale_snapshots (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time      TEXT NOT NULL,
            chain          TEXT NOT NULL,
            token_address  TEXT NOT NULL,
            token_symbol   TEXT,
            confidence     REAL DEFAULT 0,
            level          TEXT DEFAULT 'CLEAN',
            top2_hold      REAL DEFAULT 0,
            top5_hold      REAL DEFAULT 0,
            lp_usd         REAL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_whale_token ON whale_snapshots(chain, token_address, scan_time);
    """)
    conn.commit()


def collect_all_tokens(conn: sqlite3.Connection) -> list[TokenEngineData]:
    """
    从 5 个引擎的数据表中收集最新结果，合并成每代币一条记录
    """
    tokens: dict[str, TokenEngineData] = {}

    def key(chain, addr):
        return f"{chain}:{addr.lower()}"

    # ── 1. master-scan (watchlist 表) ──
    rows = conn.execute("""
        SELECT chain, token_address, token_symbol, signal_level, trigger_pattern
        FROM watchlist
        WHERE status = 'ACTIVE'
    """).fetchall()
    for r in rows:
        k = key(r["chain"], r["token_address"])
        if k not in tokens:
            tokens[k] = TokenEngineData(chain=r["chain"], token_address=r["token_address"],
                                         token_symbol=r["token_symbol"] or "?")
        tokens[k].master_signal  = r["signal_level"] or ""
        tokens[k].master_pattern = r["trigger_pattern"] or ""
        tokens[k].engine_hits += 1

    # ── 2. opus-scan ──
    rows = conn.execute("""
        SELECT o.*
        FROM opus_snapshots o
        INNER JOIN (
            SELECT chain, token_address, MAX(scan_time) AS latest
            FROM opus_snapshots GROUP BY chain, token_address
        ) m ON o.chain=m.chain AND o.token_address=m.token_address AND o.scan_time=m.latest
        WHERE o.verdict != 'NEUTRAL'
    """).fetchall()
    for r in rows:
        k = key(r["chain"], r["token_address"])
        if k not in tokens:
            tokens[k] = TokenEngineData(chain=r["chain"], token_address=r["token_address"],
                                         token_symbol=r["token_symbol"] or "?")
        tokens[k].opus_acc_conf  = r["acc_confidence"] or 0
        tokens[k].opus_dist_conf = r["dist_confidence"] or 0
        tokens[k].opus_verdict   = r["verdict"] or ""
        tokens[k].engine_hits += 1

    # ── 3. unified-scan ──
    rows = conn.execute("""
        SELECT u.*
        FROM unified_snapshots u
        INNER JOIN (
            SELECT chain, token_address, MAX(scan_time) AS latest
            FROM unified_snapshots GROUP BY chain, token_address
        ) m ON u.chain=m.chain AND u.token_address=m.token_address AND u.scan_time=m.latest
        WHERE u.signal_level != ''
    """).fetchall()
    for r in rows:
        k = key(r["chain"], r["token_address"])
        if k not in tokens:
            tokens[k] = TokenEngineData(chain=r["chain"], token_address=r["token_address"],
                                         token_symbol=r["token_symbol"] or "?")
        tokens[k].unified_signal = r["signal_level"] or ""
        tokens[k].unified_score  = r["score"] or 0
        tokens[k].engine_hits += 1

    # ── 4. whale-scan ──
    rows = conn.execute("""
        SELECT w.*
        FROM whale_snapshots w
        INNER JOIN (
            SELECT chain, token_address, MAX(scan_time) AS latest
            FROM whale_snapshots GROUP BY chain, token_address
        ) m ON w.chain=m.chain AND w.token_address=m.token_address AND w.scan_time=m.latest
        WHERE w.level != 'CLEAN'
    """).fetchall()
    for r in rows:
        k = key(r["chain"], r["token_address"])
        if k not in tokens:
            tokens[k] = TokenEngineData(chain=r["chain"], token_address=r["token_address"],
                                         token_symbol=r["token_symbol"] or "?")
        tokens[k].whale_level = r["level"] or ""
        tokens[k].whale_conf  = r["confidence"] or 0
        tokens[k].engine_hits += 1

    # ── 5. cost-basis-scan ──
    rows = conn.execute("""
        SELECT c.*
        FROM cost_basis_snapshots c
        INNER JOIN (
            SELECT chain, token_address, MAX(scan_time) AS latest
            FROM cost_basis_snapshots GROUP BY chain, token_address
        ) m ON c.chain=m.chain AND c.token_address=m.token_address AND c.scan_time=m.latest
        WHERE c.verdict != 'NEUTRAL'
    """).fetchall()
    for r in rows:
        k = key(r["chain"], r["token_address"])
        if k not in tokens:
            tokens[k] = TokenEngineData(chain=r["chain"], token_address=r["token_address"],
                                         token_symbol=r["token_symbol"] or "?")
        tokens[k].cb_verdict      = r["verdict"] or ""
        tokens[k].cb_acc_pct      = r["acc_pct"] or 0
        tokens[k].cb_dist_pct     = r["dist_pct"] or 0
        tokens[k].cb_vwap         = r["vwap"] or 0
        tokens[k].cb_gecko_price  = r["gecko_price"] or 0
        tokens[k].cb_windfall_pct = r["windfall_pct"] or 0
        tokens[k].cb_signals      = r["triggered_signals"] or ""
        tokens[k].engine_hits += 1

    return list(tokens.values())


def save_meta_result(conn: sqlite3.Connection, result: dict):
    """保存 meta-verdict 仲裁结果"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meta_snapshots (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time      TEXT NOT NULL,
            chain          TEXT NOT NULL,
            token_address  TEXT NOT NULL,
            token_symbol   TEXT,
            meta_score     REAL DEFAULT 0,
            meta_verdict   TEXT DEFAULT 'NEUTRAL',
            engine_hits    INTEGER DEFAULT 0,
            master_signal  TEXT DEFAULT '',
            opus_verdict   TEXT DEFAULT '',
            unified_signal TEXT DEFAULT '',
            whale_level    TEXT DEFAULT '',
            cb_verdict     TEXT DEFAULT '',
            stage          TEXT DEFAULT '',
            UNIQUE(chain, token_address, scan_time)
        )
    """)
    conn.execute("""
        INSERT OR REPLACE INTO meta_snapshots
        (scan_time, chain, token_address, token_symbol, meta_score, meta_verdict,
         engine_hits, master_signal, opus_verdict, unified_signal, whale_level, cb_verdict, stage)
        VALUES (:scan_time, :chain, :token_address, :token_symbol, :meta_score, :meta_verdict,
                :engine_hits, :master_signal, :opus_verdict, :unified_signal, :whale_level,
                :cb_verdict, :stage)
    """, result)
    conn.commit()
