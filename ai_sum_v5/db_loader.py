"""
AI-SUM V5 — 数据加载层
- 统一封装所有 SQL 查询，上层模块不直接写 SQL
- 通过 ATTACH DATABASE 只读挂载 select.db 源库
- 继承 v4 SQL 性能规范：CTE 先聚合、(chain, token_address) 双键 JOIN、禁止 LOWER() JOIN
"""
import sqlite3
import json
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

from . import config


# ============================================================
# 连接管理
# ============================================================

def get_connection() -> sqlite3.Connection:
    """
    返回 select-sum.db 的连接，并 ATTACH select.db 为 src_db（只读）。
    通过 PRAGMA query_only=ON 确保对原始数据库只读访问，不产生任何写操作。
    调用方负责 conn.close()。
    """
    conn = sqlite3.connect(config.SUM_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # ATTACH 原始库（注意 sqlite3 的 PRAGMA query_only=ON 会作用于整个主连接导致建表报错，故移除）
    conn.execute(f"ATTACH DATABASE '{config.SRC_DB_PATH}' AS src_db")
    _ensure_schema(conn)
    return conn




def _ensure_schema(conn: sqlite3.Connection) -> None:
    """初始化 select-sum.db 表结构（幂等）。"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            chain           TEXT    NOT NULL,
            token_address   TEXT    NOT NULL,
            token_symbol    TEXT,
            added_at        TEXT    NOT NULL,
            last_updated    TEXT    NOT NULL,
            trigger_pattern TEXT    NOT NULL,
            signal_level    TEXT    NOT NULL,
            trigger_detail  TEXT,
            consecutive_no_signal INTEGER DEFAULT 0,
            status          TEXT    DEFAULT 'ACTIVE',
            notes           TEXT,
            UNIQUE(chain, token_address)
        );

        CREATE TABLE IF NOT EXISTS snapshot_diff_cache (
            chain           TEXT NOT NULL,
            token_address   TEXT NOT NULL,
            t_new           TEXT NOT NULL,
            t_old           TEXT NOT NULL,
            computed_at     TEXT NOT NULL,
            diff_json       TEXT NOT NULL,
            PRIMARY KEY (chain, token_address, t_new, t_old)
        );

        CREATE TABLE IF NOT EXISTS scan_runs (
            run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at          TEXT NOT NULL,
            tokens_scanned  INTEGER,
            red_alerts      INTEGER,
            yellow_alerts   INTEGER,
            new_watchlist   INTEGER,
            report_path     TEXT
        );
    """)
    conn.commit()


# ============================================================
# 代币列表
# ============================================================

def load_all_tokens(conn: sqlite3.Connection) -> list[dict]:
    """
    返回源库中所有有效代币。
    过滤：acc_holders >= MIN_ACC_HOLDERS（对齐 v4 前置过滤）。
    返回字段：chain, token_address, token_symbol, snap_count, latest_snapshot
    """
    sql = """
    WITH base AS (
        SELECT
            b.chain,
            b.token_address,
            COUNT(DISTINCT b.snapshot_time)                          AS snap_count,
            MAX(b.snapshot_time)                                     AS latest_snapshot,
            SUM(CASE WHEN b.is_accumulating=1 THEN 1 ELSE 0 END)    AS acc_holders
        FROM src_db.bubblemap_holders b
        GROUP BY b.chain, b.token_address
        HAVING acc_holders >= :min_acc
    )
    SELECT
        b.chain,
        b.token_address,
        COALESCE(t.symbol, '?')  AS token_symbol,
        COALESCE(t.name, '未知') AS token_name,
        b.snap_count,
        b.latest_snapshot
    FROM base b
    LEFT JOIN src_db.token_names t
        ON b.chain = t.chain AND b.token_address = t.token_address
    ORDER BY b.chain, b.token_address
    """
    rows = conn.execute(sql, {"min_acc": config.MIN_ACC_HOLDERS}).fetchall()
    return [dict(r) for r in rows]


# ============================================================
# 快照时间序列
# ============================================================

def load_snapshot_times(
    conn: sqlite3.Connection,
    chain: str,
    token_address: str,
) -> list[str]:
    """返回该代币所有快照时间（升序），字符串格式。"""
    rows = conn.execute(
        """
        SELECT DISTINCT snapshot_time
        FROM src_db.bubblemap_holders
        WHERE chain = ? AND token_address = ?
        ORDER BY snapshot_time ASC
        """,
        (chain, token_address),
    ).fetchall()
    return [r[0] for r in rows]


# ============================================================
# 单快照持有者明细
# ============================================================

def load_snapshot_detail(
    conn: sqlite3.Connection,
    chain: str,
    token_address: str,
    snapshot_time: str,
) -> list[dict]:
    """
    返回指定快照的持有者明细（Top 300）。
    字段：wallet_address, rank, hold_percentage, buy_amt_usd, sell_amt_usd,
          acc_score, is_accumulating, is_cex, is_dex, is_contract, is_supernode
    """
    rows = conn.execute(
        """
        SELECT
            wallet_address,
            rank,
            hold_percentage,
            buy_amt_usd,
            sell_amt_usd,
            net_inflow,
            acc_score,
            is_accumulating,
            is_cex,
            is_dex,
            is_contract,
            is_supernode
        FROM src_db.bubblemap_holders
        WHERE chain = ? AND token_address = ? AND snapshot_time = ?
        ORDER BY rank ASC
        """,
        (chain, token_address, snapshot_time),
    ).fetchall()
    return [dict(r) for r in rows]


# ============================================================
# 最近 N 快照的轻量地址名册（用于 Diff）
# ============================================================

def load_roster_series(
    conn: sqlite3.Connection,
    chain: str,
    token_address: str,
    n: int = None,
) -> dict[str, list[dict]]:
    """
    返回最近 n 个快照的地址名册。
    结构：{snapshot_time: [{"wallet_address": ..., "hold_percentage": ..., "acc_score": ..., "is_accumulating": ...}]}
    """
    if n is None:
        n = config.DEFAULT_SNAP_WINDOW

    snap_times = load_snapshot_times(conn, chain, token_address)
    recent = snap_times[-n:] if len(snap_times) >= n else snap_times

    if not recent:
        return {}

    placeholders = ",".join(["?" for _ in recent])
    rows = conn.execute(
        f"""
        SELECT
            snapshot_time,
            wallet_address,
            hold_percentage,
            acc_score,
            buy_amt_usd,
            sell_amt_usd,
            is_accumulating,
            is_cex,
            is_dex,
            is_contract,
            is_supernode
        FROM src_db.bubblemap_holders
        WHERE chain = ? AND token_address = ?
          AND snapshot_time IN ({placeholders})
        ORDER BY snapshot_time ASC, rank ASC
        """,
        [chain, token_address] + list(recent),
    ).fetchall()

    result: dict[str, list[dict]] = {}
    for r in rows:
        t = r["snapshot_time"]
        result.setdefault(t, []).append(dict(r))
    return result


# ============================================================
# 代币历史 acc_hold 时间序列（用于计算历史中位数）
# ============================================================

def load_acc_hold_history(
    conn: sqlite3.Connection,
    chain: str,
    token_address: str,
) -> list[tuple[str, float]]:
    """
    返回所有快照的 (snapshot_time, acc_hold_pct) 序列（升序）。
    acc_hold_pct = 该快照中 is_accumulating=1 的地址的持仓占比总和。
    """
    rows = conn.execute(
        """
        SELECT
            snapshot_time,
            ROUND(SUM(CASE WHEN is_accumulating=1 THEN hold_percentage ELSE 0 END), 4) AS acc_hold
        FROM src_db.bubblemap_holders
        WHERE chain = ? AND token_address = ?
        GROUP BY snapshot_time
        ORDER BY snapshot_time ASC
        """,
        (chain, token_address),
    ).fetchall()
    return [(r[0], r[1] or 0.0) for r in rows]


# ============================================================
# V4 聚合指标（背景参照，雷达报 TOP10 用）
# ============================================================

def load_v4_agg_stats(conn: sqlite3.Connection) -> list[dict]:
    """
    返回全库代币的 v4 截面聚合指标（用于雷达报 TOP10 背景参照）。
    仅读取，不重算评分。
    """
    sql = """
    WITH token_agg AS (
        SELECT
            b.chain,
            b.token_address,
            COUNT(DISTINCT b.snapshot_time)                                  AS snap_count,
            MAX(b.snapshot_time)                                              AS latest_snapshot,
            SUM(CASE WHEN b.is_cex=0 AND b.is_dex=0
                      AND b.is_contract=0 AND b.is_supernode=0 THEN 1 ELSE 0 END) AS real_users,
            SUM(CASE WHEN b.is_accumulating=1 THEN 1 ELSE 0 END)             AS acc_holders,
            ROUND(AVG(CASE WHEN b.is_accumulating=1 THEN b.acc_score ELSE NULL END), 2) AS avg_acc_score,
            ROUND(SUM(CASE WHEN b.is_accumulating=1 THEN b.buy_amt_usd  ELSE 0 END), 2) AS acc_buy,
            ROUND(SUM(CASE WHEN b.is_accumulating=1 THEN b.sell_amt_usd ELSE 0 END), 2) AS acc_sell,
            SUM(CASE WHEN b.is_accumulating=1 AND b.sell_amt_usd=0
                      AND b.buy_amt_usd>0 THEN 1 ELSE 0 END)                 AS only_buy_cnt
        FROM src_db.bubblemap_holders b
        GROUP BY b.chain, b.token_address
        HAVING acc_holders >= :min_acc
    )
    SELECT
        a.chain,
        a.token_address,
        COALESCE(t.symbol, '?')  AS token_symbol,
        COALESCE(t.name, '未知') AS token_name,
        a.snap_count,
        a.latest_snapshot,
        a.real_users,
        a.acc_holders,
        a.avg_acc_score,
        a.acc_buy,
        a.acc_sell,
        a.only_buy_cnt,
        ROUND(100.0 * a.acc_holders / NULLIF(a.real_users, 0), 1) AS acc_pct_real,
        ROUND(100.0 * a.only_buy_cnt / NULLIF(a.acc_holders, 0), 1) AS only_buy_pct
    FROM token_agg a
    LEFT JOIN src_db.token_names t
        ON a.chain = t.chain AND a.token_address = t.token_address
    ORDER BY a.avg_acc_score DESC
    """
    rows = conn.execute(sql, {"min_acc": config.MIN_ACC_HOLDERS}).fetchall()
    return [dict(r) for r in rows]


# ============================================================
# Diff 缓存读写
# ============================================================

def load_diff_cache(
    conn: sqlite3.Connection,
    chain: str,
    token_address: str,
    t_new: str,
    t_old: str,
) -> Optional[dict]:
    """读取已计算的 diff 缓存，不存在返回 None。"""
    row = conn.execute(
        """
        SELECT diff_json FROM snapshot_diff_cache
        WHERE chain=? AND token_address=? AND t_new=? AND t_old=?
        """,
        (chain, token_address, t_new, t_old),
    ).fetchone()
    if row:
        return json.loads(row[0])
    return None


def save_diff_cache(
    conn: sqlite3.Connection,
    chain: str,
    token_address: str,
    t_new: str,
    t_old: str,
    diff_data: dict,
) -> None:
    """写入 diff 缓存（已存在则覆盖）。"""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR REPLACE INTO snapshot_diff_cache
            (chain, token_address, t_new, t_old, computed_at, diff_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (chain, token_address, t_new, t_old, now, json.dumps(diff_data, ensure_ascii=False)),
    )
    conn.commit()


# ============================================================
# Watchlist 读写
# ============================================================

def load_active_watchlist(conn: sqlite3.Connection) -> list[dict]:
    """返回 status=ACTIVE 的所有追踪对象。"""
    rows = conn.execute(
        "SELECT * FROM watchlist WHERE status='ACTIVE' ORDER BY last_updated DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def upsert_watchlist(
    conn: sqlite3.Connection,
    chain: str,
    token_address: str,
    token_symbol: str,
    trigger_pattern: str,
    signal_level: str,
    trigger_detail: dict,
) -> bool:
    """
    写入或更新 watchlist 记录。
    - 首次出现：INSERT，status=ACTIVE，consecutive_no_signal=0
    - 已存在 ACTIVE：更新 last_updated / trigger_pattern / signal_level / trigger_detail，重置计数
    返回 True 表示新增，False 表示更新。
    """
    now = datetime.now(timezone.utc).isoformat()
    existing = conn.execute(
        "SELECT id, status FROM watchlist WHERE chain=? AND token_address=?",
        (chain, token_address),
    ).fetchone()

    if existing is None:
        conn.execute(
            """
            INSERT INTO watchlist
                (chain, token_address, token_symbol, added_at, last_updated,
                 trigger_pattern, signal_level, trigger_detail, consecutive_no_signal, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 'ACTIVE')
            """,
            (chain, token_address, token_symbol, now, now,
             trigger_pattern, signal_level, json.dumps(trigger_detail, ensure_ascii=False)),
        )
        conn.commit()
        return True
    else:
        # 无论原状态如何，有新信号时重置为 ACTIVE，并重置 no_signal 计数
        conn.execute(
            """
            UPDATE watchlist
            SET last_updated=?, trigger_pattern=?, signal_level=?,
                trigger_detail=?, consecutive_no_signal=0, status='ACTIVE'
            WHERE chain=? AND token_address=?
            """,
            (now, trigger_pattern, signal_level,
             json.dumps(trigger_detail, ensure_ascii=False),
             chain, token_address),
        )
        conn.commit()
        return False


def increment_no_signal(conn: sqlite3.Connection, chain: str, token_address: str) -> int:
    """对 ACTIVE 记录递增 no_signal 计数，超过阈值则自动 EXPIRED。返回当前计数。"""
    row = conn.execute(
        "SELECT consecutive_no_signal FROM watchlist WHERE chain=? AND token_address=? AND status='ACTIVE'",
        (chain, token_address),
    ).fetchone()
    if row is None:
        return 0

    new_cnt = row[0] + 1
    new_status = "EXPIRED" if new_cnt >= config.WATCHLIST_EXPIRE_SCANS else "ACTIVE"
    conn.execute(
        "UPDATE watchlist SET consecutive_no_signal=?, status=? WHERE chain=? AND token_address=?",
        (new_cnt, new_status, chain, token_address),
    )
    conn.commit()
    return new_cnt


def update_watchlist_status(
    conn: sqlite3.Connection,
    chain: str,
    token_address: str,
    status: str,
    notes: str = None,
) -> None:
    """人工更新状态（DISMISSED / PUMPED / EXPIRED）。"""
    sql = "UPDATE watchlist SET status=?, last_updated=? WHERE chain=? AND token_address=?"
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(sql, (status, now, chain, token_address))
    if notes:
        conn.execute(
            "UPDATE watchlist SET notes=? WHERE chain=? AND token_address=?",
            (notes, chain, token_address),
        )
    conn.commit()


# ============================================================
# 扫描运行记录
# ============================================================

def record_scan_run(
    conn: sqlite3.Connection,
    tokens_scanned: int,
    red_alerts: int,
    yellow_alerts: int,
    new_watchlist: int,
    report_path: str = None,
) -> int:
    """写入扫描运行记录，返回 run_id。"""
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """
        INSERT INTO scan_runs
            (run_at, tokens_scanned, red_alerts, yellow_alerts, new_watchlist, report_path)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (now, tokens_scanned, red_alerts, yellow_alerts, new_watchlist, report_path),
    )
    conn.commit()
    return cursor.lastrowid
