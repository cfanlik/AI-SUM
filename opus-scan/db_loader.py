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
    """全部代币列表（直查 token_names，毫秒级响应）"""
    sql = """
    SELECT
        chain,
        token_address,
        COALESCE(symbol, '?')  AS token_symbol,
        COALESCE(name, '?')    AS token_name,
        bm_snapshot_count      AS snap_count,
        bm_last_snapshot       AS latest_snapshot,
        datetime(bm_last_snapshot, '-30 days') AS earliest_snapshot
    FROM src_db.token_names
    WHERE bm_snapshot_count >= :min_snap
    ORDER BY chain, token_address
    """
    rows = conn.execute(sql, {"min_snap": config.MIN_SNAPSHOTS}).fetchall()
    return [dict(r) for r in rows]


def load_snapshot_stats_series(
    conn: sqlite3.Connection, chain: str, addr: str, limit: int = 30
) -> list[dict]:
    """聚合统计（滑动窗口至最近 30 期快照，覆盖 15-30 天宏观吸筹周期）"""
    snaps = conn.execute("""
        SELECT DISTINCT snapshot_time
        FROM src_db.bubblemap_holders
        WHERE chain = ? AND token_address = ?
        ORDER BY snapshot_time DESC
        LIMIT ?
    """, (chain, addr, limit)).fetchall()
    if not snaps:
        return []
    min_snap = snaps[-1][0]
    rows = conn.execute("""
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
        WHERE chain = ? AND token_address = ? AND snapshot_time >= ?
        GROUP BY snapshot_time
        ORDER BY snapshot_time ASC
    """, (chain, addr, min_snap)).fetchall()
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
