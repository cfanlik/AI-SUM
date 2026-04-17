"""
whale-scan — DB 加载器
ATTACH select.db 只读，提供全部数据查询接口。
"""
import sqlite3
from pathlib import Path
import config


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(f"ATTACH DATABASE '{config.SRC_DB_PATH}' AS src")
    return conn


def load_all_tokens(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT chain, token_address, symbol FROM src.token_names"
    ).fetchall()
    return [dict(r) for r in rows]


def load_snapshot_times(conn, addr: str) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT snapshot_time FROM src.bubblemap_holders "
        "WHERE token_address = ? ORDER BY snapshot_time ASC",
        (addr,),
    ).fetchall()
    return [r["snapshot_time"] for r in rows]


def load_top_holders(conn, addr: str, snapshot: str, limit: int = 300) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM src.bubblemap_holders "
        "WHERE token_address = ? AND snapshot_time = ? "
        "ORDER BY hold_percentage DESC LIMIT ?",
        (addr, snapshot, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def load_latest_scores(conn, addr: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM src.token_scores "
        "WHERE token_address = ? ORDER BY scan_time DESC LIMIT 1",
        (addr,),
    ).fetchone()
    return dict(row) if row else None


def load_latest_gecko(conn, addr: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM src.gecko_market_data "
        "WHERE token_address = ? ORDER BY scan_time DESC LIMIT 1",
        (addr,),
    ).fetchone()
    return dict(row) if row else None


def load_acc_stats_series(conn, addr: str) -> list[dict]:
    """每个快照的吸筹统计"""
    rows = conn.execute(
        "SELECT snapshot_time, "
        "COUNT(*) as total, "
        "SUM(CASE WHEN is_accumulating=1 THEN 1 ELSE 0 END) as acc_cnt, "
        "SUM(CASE WHEN is_accumulating=1 THEN hold_percentage ELSE 0 END) as acc_pct "
        "FROM src.bubblemap_holders WHERE token_address = ? "
        "GROUP BY snapshot_time ORDER BY snapshot_time ASC",
        (addr,),
    ).fetchall()
    return [dict(r) for r in rows]
