"""
opus-scan — 数据加载层
ATTACH select.db 只读，封装全部 SQL 查询。
"""
import sqlite3
from typing import Optional
import config


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(config.SUM_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"ATTACH DATABASE '{config.SRC_DB_PATH}' AS src_db")
    return conn


def load_all_tokens(conn: sqlite3.Connection) -> list[dict]:
    """全部代币列表（≥ MIN_SNAPSHOTS 快照 + ≥3 吸筹地址）"""
    sql = """
    WITH base AS (
        SELECT chain, token_address,
               COUNT(DISTINCT snapshot_time) AS snap_count,
               MAX(snapshot_time) AS latest_snapshot,
               MIN(snapshot_time) AS earliest_snapshot
        FROM src_db.bubblemap_holders
        GROUP BY chain, token_address
        HAVING snap_count >= :min_snap
    )
    SELECT b.chain, b.token_address,
           COALESCE(t.symbol, '?') AS token_symbol,
           COALESCE(t.name, '?')   AS token_name,
           b.snap_count, b.latest_snapshot, b.earliest_snapshot
    FROM base b
    LEFT JOIN src_db.token_names t
        ON b.chain = t.chain AND b.token_address = t.token_address
    ORDER BY b.chain, b.token_address
    """
    rows = conn.execute(sql, {"min_snap": config.MIN_SNAPSHOTS}).fetchall()
    return [dict(r) for r in rows]


def load_snapshot_stats_series(
    conn: sqlite3.Connection, chain: str, addr: str
) -> list[dict]:
    """全部快照的聚合统计"""
    sql = """
    SELECT
        snapshot_time,
        COUNT(*) AS total,
        SUM(CASE WHEN is_accumulating=1 THEN 1 ELSE 0 END) AS acc_cnt,
        AVG(acc_score) AS avg_score,
        SUM(CASE WHEN is_accumulating=1 THEN hold_percentage ELSE 0 END) AS acc_hold_pct,
        SUM(CASE WHEN is_cex=1 THEN hold_percentage ELSE 0 END) AS cex_hold,
        SUM(CASE WHEN is_contract=1 THEN hold_percentage ELSE 0 END) AS contract_hold,
        SUM(CASE WHEN is_dex=1 THEN hold_percentage ELSE 0 END) AS dex_hold,
        SUM(CASE WHEN is_supernode=1 THEN 1 ELSE 0 END) AS supernode_cnt,
        SUM(CASE WHEN hold_percentage >= 2.0
            AND is_cex=0 AND is_contract=0 AND is_supernode=0
            THEN 1 ELSE 0 END) AS hidden_whale
    FROM src_db.bubblemap_holders
    WHERE chain=? AND token_address=?
    GROUP BY snapshot_time
    ORDER BY snapshot_time ASC
    """
    rows = conn.execute(sql, (chain, addr)).fetchall()
    return [dict(r) for r in rows]


def load_top_holders(
    conn: sqlite3.Connection, chain: str, addr: str,
    snapshot_time: str, limit: int = 30
) -> list[dict]:
    """单快照 Top N holders 明细"""
    sql = """
    SELECT
        wallet_address, hold_percentage, acc_score,
        is_accumulating, is_cex, is_dex, is_contract, is_supernode,
        buy_cnt, sell_cnt, buy_amt_usd, sell_amt_usd, net_inflow, dex_ratio, entity_id,
        COALESCE(recent_48h_in, 0) AS recent_48h_in,
        COALESCE(recent_48h_out, 0) AS recent_48h_out,
        COALESCE(surge_ratio, 0) AS surge_ratio
    FROM src_db.bubblemap_holders
    WHERE chain=? AND token_address=? AND snapshot_time=?
    ORDER BY hold_percentage DESC
    LIMIT ?
    """
    rows = conn.execute(sql, (chain, addr, snapshot_time, limit)).fetchall()
    return [dict(r) for r in rows]


def load_gecko_latest(
    conn: sqlite3.Connection, chain: str, addr: str
) -> Optional[dict]:
    """Gecko 市场数据最新 1 行"""
    try:
        row = conn.execute(
            "SELECT * FROM src_db.gecko_market_data WHERE chain=? AND token_address=? ORDER BY rowid DESC LIMIT 1",
            (chain, addr)
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
