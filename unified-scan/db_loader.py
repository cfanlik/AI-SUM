# ──────────────────────────────────────────────────────────
# 信号编码速查表 (Signal Code Reference)
# ──────────────────────────────────────────────────────────
# A1(DIAMOND/RED)  — BubbleMap 吸筹标签等级
# A2(YELLOW/RED)   — 二级吸筹指标（YELLOW=中等, RED=强）
# A4(CEX流出)      — 代币从 CEX 转出到链上 → 买入持有
# D1(CEX流入)      — 代币流入 CEX → 准备卖出
# D2(出货者)       — 检测到出货行为的地址
# D3(被动漂移)     — 持仓未变但价格下跌，被动承受亏损
# S1(极端集中)     — Top 地址持仓极度集中
# S2(M/L=Nx)       — 市值/流动性比 → 越高越脆弱
# S4(V/L=x)        — 换手效率 → V/L>10 极端换手（标记不计分）
# G2(LP=$x)        — LP 流动性门控 → <$30K降级, <$10K否决
# G3(死池)         — V/L<0.01 + Vol<$100 → 否决ACC信号
# ──────────────────────────────────────────────────────────

"""
unified-scan — 数据加载层
ATTACH select.db 只读 + select-sum.db 读写。
合并 master-scan/opus-scan/bigcoin 三框架的最佳查询模式。
"""
import sqlite3
import json
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

import config


def get_connection() -> sqlite3.Connection:
    """返回 select-sum.db 连接，ATTACH select.db 为 src_db（只读）。"""
    conn = sqlite3.connect(config.SUM_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"ATTACH DATABASE '{config.SRC_DB_PATH}' AS src_db")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """初始化 unified_results + daily_summary 表（幂等）。"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS unified_results (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time       TEXT    NOT NULL,
            chain           TEXT    NOT NULL,
            token_address   TEXT    NOT NULL,
            token_symbol    TEXT,
            acc_score       REAL    DEFAULT 0,
            dist_score      REAL    DEFAULT 0,
            struct_risk     REAL    DEFAULT 0,
            verdict         TEXT    DEFAULT 'NEUTRAL',
            acc_cnt         INTEGER,
            acc_hold_pct    REAL,
            dex_verified_pct REAL,
            cex_hold_pct    REAL,
            cex_delta_pct   REAL,
            top2_hold       REAL,
            top10_hold      REAL,
            institutional_hold REAL,
            hidden_whale_cnt INTEGER,
            mcap_liq_ratio  REAL,
            triggered_signals TEXT,
            signal_details    TEXT,
            UNIQUE(scan_time, chain, token_address)
        );

        CREATE TABLE IF NOT EXISTS daily_summary (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_date       TEXT    NOT NULL,
            total_tokens    INTEGER,
            diamond_count   INTEGER,
            strong_acc_count INTEGER,
            dist_count      INTEGER,
            whale_dump_count INTEGER,
            new_alerts      TEXT,
            removed_alerts  TEXT,
            UNIQUE(scan_date)
        );

        CREATE INDEX IF NOT EXISTS idx_unified_token ON unified_results(chain, token_address);
        CREATE INDEX IF NOT EXISTS idx_unified_time  ON unified_results(scan_time);
        CREATE INDEX IF NOT EXISTS idx_unified_verdict ON unified_results(verdict);
    """)
    conn.commit()


# ════════════════════════════════════════════════════════════
# 代币列表
# ════════════════════════════════════════════════════════════

def load_all_tokens(conn: sqlite3.Connection) -> list[dict]:
    """全部有效代币（≥MIN_SNAPSHOTS快照 + ≥MIN_ACC_HOLDERS吸筹地址）"""
    sql = """
    WITH base AS (
        SELECT chain, token_address,
               COUNT(DISTINCT snapshot_time) AS snap_count,
               MAX(snapshot_time) AS latest_snapshot,
               MIN(snapshot_time) AS earliest_snapshot,
               SUM(CASE WHEN is_accumulating=1 THEN 1 ELSE 0 END) AS acc_holders
        FROM src_db.bubblemap_holders
        GROUP BY chain, token_address
        HAVING snap_count >= :min_snap AND acc_holders >= :min_acc
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
    rows = conn.execute(sql, {
        "min_snap": config.MIN_SNAPSHOTS,
        "min_acc": config.MIN_ACC_HOLDERS,
    }).fetchall()
    return [dict(r) for r in rows]


# ════════════════════════════════════════════════════════════
# 快照时间列表
# ════════════════════════════════════════════════════════════

def load_snapshot_times(
    conn: sqlite3.Connection, chain: str, token_address: str
) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT snapshot_time FROM src_db.bubblemap_holders "
        "WHERE chain=? AND token_address=? ORDER BY snapshot_time ASC",
        (chain, token_address),
    ).fetchall()
    return [r[0] for r in rows]


# ════════════════════════════════════════════════════════════
# 快照持有者明细（Top N 或全量300）
# ════════════════════════════════════════════════════════════

def load_snapshot_holders(
    conn: sqlite3.Connection, chain: str, token_address: str,
    snapshot_time: str, limit: int = 300
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT wallet_address, rank, hold_percentage,
               buy_amt_usd, sell_amt_usd, buy_cnt, sell_cnt,
               net_inflow, acc_score, is_accumulating,
               is_cex, is_dex, is_contract, is_supernode,
               dex_ratio, dex_ratio_hop2, gmgn_verified, entity_id,
               COALESCE(recent_48h_in, 0) AS recent_48h_in,
               COALESCE(recent_48h_out, 0) AS recent_48h_out,
               COALESCE(surge_ratio, 0) AS surge_ratio
        FROM src_db.bubblemap_holders
        WHERE chain=? AND token_address=? AND snapshot_time=?
        ORDER BY hold_percentage DESC
        LIMIT ?
        """,
        (chain, token_address, snapshot_time, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# ════════════════════════════════════════════════════════════
# 名册系列（最近N个快照，用于diff）
# ════════════════════════════════════════════════════════════

def load_roster_series(
    conn: sqlite3.Connection, chain: str, token_address: str,
    n: int = None
) -> dict[str, list[dict]]:
    if n is None:
        n = config.DEFAULT_SNAP_WINDOW
    snap_times = load_snapshot_times(conn, chain, token_address)
    recent = snap_times[-n:] if len(snap_times) >= n else snap_times
    if not recent:
        return {}

    placeholders = ",".join(["?" for _ in recent])
    rows = conn.execute(
        f"""
        SELECT snapshot_time, wallet_address, hold_percentage,
               acc_score, buy_amt_usd, sell_amt_usd, buy_cnt, sell_cnt,
               is_accumulating, is_cex, is_dex, is_contract, is_supernode,
               dex_ratio, dex_ratio_hop2, gmgn_verified, entity_id
        FROM src_db.bubblemap_holders
        WHERE chain=? AND token_address=?
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


# ════════════════════════════════════════════════════════════
# 全快照聚合统计（CEX时序用）
# ════════════════════════════════════════════════════════════

def load_snapshot_stats_series(
    conn: sqlite3.Connection, chain: str, addr: str
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT snapshot_time,
            COUNT(*) AS total,
            SUM(CASE WHEN is_accumulating=1 THEN 1 ELSE 0 END) AS acc_cnt,
            SUM(CASE WHEN is_accumulating=1 THEN hold_percentage ELSE 0 END) AS acc_hold_pct,
            SUM(CASE WHEN is_cex=1 THEN hold_percentage ELSE 0 END) AS cex_hold,
            SUM(CASE WHEN is_contract=1 THEN hold_percentage ELSE 0 END) AS contract_hold,
            SUM(CASE WHEN hold_percentage >= 2.0
                AND is_cex=0 AND is_contract=0 AND is_supernode=0
                THEN 1 ELSE 0 END) AS hidden_whale
        FROM src_db.bubblemap_holders
        WHERE chain=? AND token_address=?
        GROUP BY snapshot_time
        ORDER BY snapshot_time ASC
        """,
        (chain, addr),
    ).fetchall()
    return [dict(r) for r in rows]


# ════════════════════════════════════════════════════════════
# Gecko 市场数据
# ════════════════════════════════════════════════════════════

def load_gecko_latest(
    conn: sqlite3.Connection, chain: str, addr: str
) -> Optional[dict]:
    try:
        row = conn.execute(
            "SELECT * FROM src_db.gecko_market_data "
            "WHERE chain=? AND token_address=? ORDER BY rowid DESC LIMIT 1",
            (chain, addr),
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


# ════════════════════════════════════════════════════════════
# token_scores 最新评分
# ════════════════════════════════════════════════════════════

def load_latest_scores(
    conn: sqlite3.Connection, chain: str, addr: str
) -> Optional[dict]:
    try:
        row = conn.execute(
            "SELECT * FROM src_db.token_scores "
            "WHERE chain=? AND token_address=? ORDER BY scan_time DESC LIMIT 1",
            (chain, addr),
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


# ════════════════════════════════════════════════════════════
# 历史 acc_hold 序列（中位数计算用）
# ════════════════════════════════════════════════════════════

def load_acc_hold_history(
    conn: sqlite3.Connection, chain: str, token_address: str
) -> list[tuple[str, float]]:
    rows = conn.execute(
        """
        SELECT snapshot_time,
               ROUND(SUM(CASE WHEN is_accumulating=1 THEN hold_percentage ELSE 0 END), 4) AS acc_hold
        FROM src_db.bubblemap_holders
        WHERE chain=? AND token_address=?
        GROUP BY snapshot_time
        ORDER BY snapshot_time ASC
        """,
        (chain, token_address),
    ).fetchall()
    return [(r[0], r[1] or 0.0) for r in rows]


# ════════════════════════════════════════════════════════════
# 持久化：写入结果
# ════════════════════════════════════════════════════════════

def save_unified_result(conn: sqlite3.Connection, result: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO unified_results
            (scan_time, chain, token_address, token_symbol,
             acc_score, dist_score, struct_risk, verdict,
             acc_cnt, acc_hold_pct, dex_verified_pct,
             cex_hold_pct, cex_delta_pct,
             top2_hold, top10_hold, institutional_hold,
             hidden_whale_cnt, mcap_liq_ratio,
             triggered_signals, signal_details)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            result["scan_time"], result["chain"], result["token_address"],
            result.get("token_symbol", "?"),
            result.get("acc_score", 0), result.get("dist_score", 0),
            result.get("struct_risk", 0), result.get("verdict", "NEUTRAL"),
            result.get("acc_cnt"), result.get("acc_hold_pct"),
            result.get("dex_verified_pct"), result.get("cex_hold_pct"),
            result.get("cex_delta_pct"), result.get("top2_hold"),
            result.get("top10_hold"), result.get("institutional_hold"),
            result.get("hidden_whale_cnt"), result.get("mcap_liq_ratio"),
            json.dumps(result.get("triggered_signals", []), ensure_ascii=False),
            json.dumps(result.get("signal_details", {}), ensure_ascii=False),
        ),
    )


def save_daily_summary(conn: sqlite3.Connection, summary: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO daily_summary
            (scan_date, total_tokens, diamond_count, strong_acc_count,
             dist_count, whale_dump_count, new_alerts, removed_alerts)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            summary["scan_date"], summary["total_tokens"],
            summary.get("diamond_count", 0), summary.get("strong_acc_count", 0),
            summary.get("dist_count", 0), summary.get("whale_dump_count", 0),
            json.dumps(summary.get("new_alerts", []), ensure_ascii=False),
            json.dumps(summary.get("removed_alerts", []), ensure_ascii=False),
        ),
    )
    conn.commit()


def load_previous_scan_verdicts(conn: sqlite3.Connection) -> dict:
    """获取上次扫描的 verdict 映射 {chain:addr → verdict}"""
    try:
        rows = conn.execute(
            """
            SELECT chain, token_address, verdict FROM unified_results
            WHERE scan_time = (SELECT MAX(scan_time) FROM unified_results)
            """
        ).fetchall()
        return {f"{r['chain']}:{r['token_address']}": r["verdict"] for r in rows}
    except Exception:
        return {}
