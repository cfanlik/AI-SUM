"""
cost-basis-scan 数据库加载器
ATTACH select.db (只读) + select-sum.db (读写)
"""
from __future__ import annotations
import sqlite3
import logging
from dataclasses import dataclass
from typing import Optional
import config

logger = logging.getLogger(__name__)


@dataclass
class CostHolder:
    """带成本数据的持有者"""
    chain: str
    token_address: str
    snapshot_time: str
    rank: int
    wallet_address: str
    hold_amount: float
    hold_percentage: float
    buy_cnt: int
    sell_cnt: int
    gmgn_avg_price: float
    gmgn_buy_amount: float
    gmgn_buy_cost_usd: float
    is_cex: int
    is_dex: int
    is_contract: int
    is_new_buyer: int
    recent_48h_in: float
    recent_48h_out: float


@dataclass
class GeckoSnapshot:
    """Gecko 市场快照"""
    price_usd: float
    reserve_usd: float
    volume_24h: float
    fdv_usd: float
    market_cap_usd: float
    vl_ratio: float
    mcap_liq_ratio: float
    buys_24h: int
    sells_24h: int
    buyers_24h: int
    sellers_24h: int
    price_change_24h: float


@dataclass
class TokenInfo:
    """代币基本信息"""
    chain: str
    token_address: str
    token_symbol: str
    snap_count: int


def get_connection() -> sqlite3.Connection:
    """获取 select-sum.db 连接并 ATTACH select.db 只读"""
    conn = sqlite3.connect(config.SUM_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # ATTACH select.db 只读
    conn.execute(f"ATTACH DATABASE 'file:{config.SRC_DB_PATH}?mode=ro' AS src")
    return conn


def ensure_tables(conn: sqlite3.Connection):
    """确保 cost_basis_snapshots 表存在"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cost_basis_snapshots (
            chain               TEXT NOT NULL,
            token_address       TEXT NOT NULL,
            scan_time           TEXT NOT NULL,
            token_symbol        TEXT DEFAULT '',
            verdict             TEXT DEFAULT 'NEUTRAL',
            acc_pct             REAL DEFAULT 0,
            dist_pct            REAL DEFAULT 0,
            vwap                REAL DEFAULT 0,
            gecko_price         REAL DEFAULT 0,
            cost_gravity        REAL DEFAULT 0,
            gravity_drift_ratio REAL DEFAULT 0,
            windfall_pct        REAL DEFAULT 0,
            underwater_pct      REAL DEFAULT 0,
            cost_cv             REAL DEFAULT 0,
            cost_holders_count  INTEGER DEFAULT 0,
            triggered_signals   TEXT DEFAULT '',
            watchlist_ref       TEXT DEFAULT '',
            PRIMARY KEY (chain, token_address, scan_time)
        )
    """)
    conn.commit()


def load_all_tokens(conn: sqlite3.Connection) -> list[TokenInfo]:
    """加载所有代币列表"""
    rows = conn.execute("""
        SELECT chain, token_address,
               COUNT(DISTINCT snapshot_time) as snap_count
        FROM src.bubblemap_holders
        GROUP BY chain, token_address
    """).fetchall()

    tokens = []
    for r in rows:
        # 获取 symbol
        sym_row = conn.execute("""
            SELECT symbol FROM src.token_names
            WHERE chain = ? AND token_address = ?
            LIMIT 1
        """, (r["chain"], r["token_address"])).fetchone()
        symbol = sym_row["symbol"] if sym_row else "?"

        tokens.append(TokenInfo(
            chain=r["chain"],
            token_address=r["token_address"],
            token_symbol=symbol,
            snap_count=r["snap_count"],
        ))
    return tokens


def load_cost_holders(
    conn: sqlite3.Connection,
    chain: str,
    token_address: str,
    gecko_price: float,
) -> list[CostHolder]:
    """
    加载指定代币最新快照中有成本数据的持有者。
    包含脏数据清洗：
    - gmgn_avg_price = 0 或 NULL → 跳过
    - gmgn_avg_price > gecko_price * G5 → 跳过
    """
    # 取最新有成本数据的快照
    snap_row = conn.execute("""
        SELECT snapshot_time
        FROM src.bubblemap_holders
        WHERE chain = ? AND token_address = ?
          AND gmgn_avg_price IS NOT NULL AND gmgn_avg_price > 0
        ORDER BY snapshot_time DESC
        LIMIT 1
    """, (chain, token_address)).fetchone()

    if not snap_row:
        return []

    snapshot_time = snap_row["snapshot_time"]

    rows = conn.execute("""
        SELECT chain, token_address, snapshot_time, rank,
               wallet_address, hold_amount, hold_percentage,
               buy_cnt, sell_cnt,
               gmgn_avg_price, gmgn_buy_amount,
               COALESCE(gmgn_buy_cost_usd, 0) as gmgn_buy_cost_usd,
               is_cex, is_dex, is_contract, is_new_buyer,
               recent_48h_in, recent_48h_out
        FROM src.bubblemap_holders
        WHERE chain = ? AND token_address = ? AND snapshot_time = ?
          AND gmgn_avg_price IS NOT NULL AND gmgn_avg_price > 0
        ORDER BY rank ASC
    """, (chain, token_address, snapshot_time)).fetchall()

    holders = []
    outlier_limit = gecko_price * config.G5_PRICE_OUTLIER_MULT if gecko_price > 0 else float("inf")

    for r in rows:
        avg_price = r["gmgn_avg_price"]
        # G5 脏数据门
        if avg_price > outlier_limit:
            logger.debug(f"G5 脏数据剔除: {r['wallet_address']} avg_price={avg_price:.6f} > limit={outlier_limit:.6f}")
            continue

        holders.append(CostHolder(
            chain=r["chain"],
            token_address=r["token_address"],
            snapshot_time=r["snapshot_time"],
            rank=r["rank"],
            wallet_address=r["wallet_address"],
            hold_amount=r["hold_amount"] or 0,
            hold_percentage=r["hold_percentage"] or 0,
            buy_cnt=r["buy_cnt"] or 0,
            sell_cnt=r["sell_cnt"] or 0,
            gmgn_avg_price=avg_price,
            gmgn_buy_amount=r["gmgn_buy_amount"] or 0,
            gmgn_buy_cost_usd=r["gmgn_buy_cost_usd"] or 0,
            is_cex=r["is_cex"] or 0,
            is_dex=r["is_dex"] or 0,
            is_contract=r["is_contract"] or 0,
            is_new_buyer=r["is_new_buyer"] or 0,
            recent_48h_in=r["recent_48h_in"] or 0,
            recent_48h_out=r["recent_48h_out"] or 0,
        ))

    return holders


def load_previous_cost_holders(
    conn: sqlite3.Connection,
    chain: str,
    token_address: str,
) -> list[CostHolder]:
    """加载上一个有成本数据的快照（用于对比 buy_cnt 变化）"""
    snaps = conn.execute("""
        SELECT DISTINCT snapshot_time
        FROM src.bubblemap_holders
        WHERE chain = ? AND token_address = ?
          AND gmgn_avg_price IS NOT NULL AND gmgn_avg_price > 0
        ORDER BY snapshot_time DESC
        LIMIT 2
    """, (chain, token_address)).fetchall()

    if len(snaps) < 2:
        return []

    prev_snap = snaps[1]["snapshot_time"]

    rows = conn.execute("""
        SELECT chain, token_address, snapshot_time, rank,
               wallet_address, hold_amount, hold_percentage,
               buy_cnt, sell_cnt,
               gmgn_avg_price, gmgn_buy_amount,
               COALESCE(gmgn_buy_cost_usd, 0) as gmgn_buy_cost_usd,
               is_cex, is_dex, is_contract, is_new_buyer,
               recent_48h_in, recent_48h_out
        FROM src.bubblemap_holders
        WHERE chain = ? AND token_address = ? AND snapshot_time = ?
          AND gmgn_avg_price IS NOT NULL AND gmgn_avg_price > 0
        ORDER BY rank ASC
    """, (chain, token_address, prev_snap)).fetchall()

    return [CostHolder(
        chain=r["chain"], token_address=r["token_address"],
        snapshot_time=r["snapshot_time"], rank=r["rank"],
        wallet_address=r["wallet_address"],
        hold_amount=r["hold_amount"] or 0,
        hold_percentage=r["hold_percentage"] or 0,
        buy_cnt=r["buy_cnt"] or 0, sell_cnt=r["sell_cnt"] or 0,
        gmgn_avg_price=r["gmgn_avg_price"],
        gmgn_buy_amount=r["gmgn_buy_amount"] or 0,
        gmgn_buy_cost_usd=r["gmgn_buy_cost_usd"] or 0,
        is_cex=r["is_cex"] or 0, is_dex=r["is_dex"] or 0,
        is_contract=r["is_contract"] or 0,
        is_new_buyer=r["is_new_buyer"] or 0,
        recent_48h_in=r["recent_48h_in"] or 0,
        recent_48h_out=r["recent_48h_out"] or 0,
    ) for r in rows]


def load_gecko_latest(
    conn: sqlite3.Connection,
    chain: str,
    token_address: str,
) -> Optional[GeckoSnapshot]:
    """加载最新 Gecko 市场数据"""
    row = conn.execute("""
        SELECT price_usd, reserve_usd, volume_24h, fdv_usd,
               market_cap_usd, vl_ratio, mcap_liq_ratio,
               buys_24h, sells_24h, buyers_24h, sellers_24h,
               price_change_24h
        FROM src.gecko_market_data
        WHERE chain = ? AND token_address = ?
        ORDER BY scan_time DESC
        LIMIT 1
    """, (chain, token_address)).fetchone()

    if not row or not row["price_usd"]:
        return None

    return GeckoSnapshot(
        price_usd=row["price_usd"] or 0,
        reserve_usd=row["reserve_usd"] or 0,
        volume_24h=row["volume_24h"] or 0,
        fdv_usd=row["fdv_usd"] or 0,
        market_cap_usd=row["market_cap_usd"] or 0,
        vl_ratio=row["vl_ratio"] or 0,
        mcap_liq_ratio=row["mcap_liq_ratio"] or 0,
        buys_24h=row["buys_24h"] or 0,
        sells_24h=row["sells_24h"] or 0,
        buyers_24h=row["buyers_24h"] or 0,
        sellers_24h=row["sellers_24h"] or 0,
        price_change_24h=row["price_change_24h"] or 0,
    )


def load_watchlist_entry(
    conn: sqlite3.Connection,
    chain: str,
    token_address: str,
) -> Optional[dict]:
    """加载 watchlist 中该代币的历史记录"""
    row = conn.execute("""
        SELECT signal_level, trigger_pattern, status, added_at, last_updated
        FROM watchlist
        WHERE chain = ? AND token_address = ?
        LIMIT 1
    """, (chain, token_address)).fetchone()

    if not row:
        return None
    return dict(row)


def load_last_scan_result(
    conn: sqlite3.Connection,
    chain: str,
    token_address: str,
) -> Optional[dict]:
    """加载上次 cost-basis-scan 的结果"""
    row = conn.execute("""
        SELECT * FROM cost_basis_snapshots
        WHERE chain = ? AND token_address = ?
        ORDER BY scan_time DESC
        LIMIT 1
    """, (chain, token_address)).fetchone()

    if not row:
        return None
    return dict(row)


def save_scan_result(conn: sqlite3.Connection, result: dict):
    """保存本次扫描结果到 cost_basis_snapshots"""
    conn.execute("""
        INSERT OR REPLACE INTO cost_basis_snapshots
        (chain, token_address, scan_time, token_symbol, verdict,
         acc_pct, dist_pct, vwap, gecko_price, cost_gravity,
         gravity_drift_ratio, windfall_pct, underwater_pct,
         cost_cv, cost_holders_count, triggered_signals, watchlist_ref)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        result["chain"], result["token_address"], result["scan_time"],
        result.get("token_symbol", ""),
        result.get("verdict", "NEUTRAL"),
        result.get("acc_pct", 0), result.get("dist_pct", 0),
        result.get("vwap", 0), result.get("gecko_price", 0),
        result.get("cost_gravity", 0), result.get("gravity_drift_ratio", 0),
        result.get("windfall_pct", 0), result.get("underwater_pct", 0),
        result.get("cost_cv", 0), result.get("cost_holders_count", 0),
        result.get("triggered_signals", ""),
        result.get("watchlist_ref", ""),
    ))
    conn.commit()
