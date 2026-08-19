"""
meta-verdict 数据收集器
从 select-sum.db 读取 5 个引擎的最新扫描结果
"""
from __future__ import annotations
import os
import sqlite3
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("meta-verdict")


@dataclass
class TokenEngineData:
    chain: str
    token_address: str
    token_symbol: str = "?"
    engine_hits: int = 0
    diff_acc_count:  int = 0
    diff_acc_score:  float = 0.0
    vl_ratio:        float = 0.0
    new_acc_count:   int = 0
    exit_acc_count:  int = 0
    diff_stay_score: float = 0.0
    diff_total_score:float = 0.0

    # master-scan
    master_signal:  str = ""
    master_pattern: str = ""

    # opus-scan
    opus_acc_conf:  float = 0.0
    opus_dist_conf: float = 0.0
    opus_verdict:   str = ""

    # unified-scan
    unified_signal: str = ""
    unified_score:  float = 0.0

    # whale-scan
    whale_level: str = ""
    whale_conf:  float = 0.0

    # cost-basis-scan
    cb_verdict:      str   = ""
    cb_acc_pct:      float = 0.0
    cb_dist_pct:     float = 0.0
    cb_vwap:         float = 0.0
    cb_gecko_price:  float = 0.0
    cb_windfall_pct: float = 0.0
    cb_signals:      str   = ""


def get_connection() -> sqlite3.Connection:
    import config
    conn = sqlite3.connect(config.SUM_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tables(conn: sqlite3.Connection):
    """确保所有表存在"""
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
    # 包含 EXPIRED 代币 — 它们仍有历史信号价值
    rows = conn.execute("""
        SELECT chain, token_address, token_symbol, signal_level, trigger_pattern, status
        FROM watchlist
    """).fetchall()
    for r in rows:
        k = key(r["chain"], r["token_address"])
        if k not in tokens:
            tokens[k] = TokenEngineData(chain=r["chain"], token_address=r["token_address"],
                                         token_symbol=r["token_symbol"] or "?")
        # ACTIVE 代币直接计分；EXPIRED 代币降一级
        status = r["status"] or "ACTIVE"
        signal = r["signal_level"] or ""
        if status == "ACTIVE":
            tokens[k].master_signal = signal
        else:
            # EXPIRED: DIAMOND→YELLOW, RED→YELLOW, YELLOW→保留
            expired_map = {"DIAMOND": "YELLOW", "RED": "YELLOW", "YELLOW": "YELLOW"}
            tokens[k].master_signal = expired_map.get(signal, "")
        tokens[k].master_pattern = r["trigger_pattern"] or ""
        if tokens[k].master_signal:
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

    # ── 3. unified-scan (读 unified_results 表) ──
    rows = conn.execute("""
        SELECT u.*
        FROM unified_results u
        INNER JOIN (
            SELECT chain, token_address, MAX(scan_time) AS latest
            FROM unified_results GROUP BY chain, token_address
        ) m ON u.chain=m.chain AND u.token_address=m.token_address AND u.scan_time=m.latest
        WHERE u.verdict NOT IN ('NEUTRAL', '')
    """).fetchall()
    for r in rows:
        k = key(r["chain"], r["token_address"])
        if k not in tokens:
            tokens[k] = TokenEngineData(chain=r["chain"], token_address=r["token_address"],
                                         token_symbol=r["token_symbol"] or "?")
        verdict = r["verdict"] or ""
        signal_map = {"DIAMOND": "DIAMOND", "STRONG_ACC": "RED", "MODERATE_ACC": "YELLOW",
                      "WHALE_DUMP": "WHALE_DUMP", "SLOW_DISTRIBUTION": "SLOW_DIST"}
        tokens[k].unified_signal = signal_map.get(verdict, verdict)
        tokens[k].unified_score  = r["acc_score"] or 0
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

    # ── 价格补全与时序风险差分分析 (Gemini 3.5 降权回溯版) ──
    try:
        src_db = os.environ.get("SRC_DB_PATH", "/opt/select-coin/data/select.db")
        conn.execute(f"ATTACH '{src_db}' AS src")
        for k, t in tokens.items():
            # 价格补全
            if t.cb_gecko_price == 0:
                row = conn.execute(
                    "SELECT price_usd FROM src.gecko_market_data "
                    "WHERE chain=? AND token_address=? AND price_usd>0 "
                    "ORDER BY scan_time DESC LIMIT 1",
                    (t.chain, t.token_address)
                ).fetchone()
                if row:
                    t.cb_gecko_price = row[0]
            
            # 时序与抗稀释特征计算
            try:
                row_vl = conn.execute(
                    "SELECT vl_ratio FROM src.gecko_market_data "
                    "WHERE chain=? AND token_address=? AND vl_ratio>=0 "
                    "ORDER BY scan_time DESC LIMIT 1", (t.chain, t.token_address)
                ).fetchone()
                t.vl_ratio = row_vl[0] if row_vl else 0.0

                rows_snap = conn.execute("""
                    SELECT snapshot_time, wallet_address, is_accumulating, acc_score
                    FROM src.bubblemap_holders
                    WHERE chain = ? AND token_address = ? AND snapshot_time IN (
                        SELECT DISTINCT snapshot_time FROM src.bubblemap_holders
                        WHERE chain = ? AND token_address = ?
                        ORDER BY snapshot_time DESC LIMIT 5
                    )
                """, (t.chain, t.token_address, t.chain, t.token_address)).fetchall()

                snaps = {}
                for r in rows_snap:
                    snaps.setdefault(r["snapshot_time"], {})[r["wallet_address"].lower()] = (r["is_accumulating"], r["acc_score"])
                
                times = sorted(snaps.keys(), reverse=True)
                if len(times) >= 2:
                    curr_time = times[0]
                    prev_time = None
                    from datetime import datetime
                    curr_dt = datetime.strptime(curr_time, "%Y-%m-%d %H:%M:%S")
                    
                    # ── 时间序列向后滑动回溯 ──
                    for t_str in times[1:]:
                        t_dt = datetime.strptime(t_str, "%Y-%m-%d %H:%M:%S")
                        if (curr_dt - t_dt).total_seconds() >= 600:
                            prev_time = t_str
                            break
                    
                    if not prev_time and len(times) >= 2:
                        prev_time = times[1]

                    if prev_time:
                        curr_acc = {addr: sc for addr, (is_acc, sc) in snaps[curr_time].items() if is_acc}
                        prev_acc = {addr: sc for addr, (is_acc, sc) in snaps[prev_time].items() if is_acc}
                        
                        t.diff_acc_count = len(curr_acc) - len(prev_acc)
                        t.new_acc_count = len(set(curr_acc.keys()) - set(prev_acc.keys()))
                        t.exit_acc_count = len(set(prev_acc.keys()) - set(curr_acc.keys()))
                        t.diff_total_score = round(sum(curr_acc.values()) - sum(prev_acc.values()), 2)
                        
                        stay_addrs = set(curr_acc.keys()) & set(prev_acc.keys())
                        if stay_addrs:
                            curr_stay_avg = sum(curr_acc[a] for a in stay_addrs) / len(stay_addrs)
                            prev_stay_avg = sum(prev_acc[a] for a in stay_addrs) / len(stay_addrs)
                            t.diff_stay_score = round(curr_stay_avg - prev_stay_avg, 2)
                        else:
                            t.diff_stay_score = 0.0
                            prev_stay_avg = sum(prev_acc.values()) / len(prev_acc) if prev_acc else 50.0

                        # ── 门差分动态降权模型 (防稀释补丁) ──
                        new_addrs = set(curr_acc.keys()) - set(prev_acc.keys())
                        weighted_curr_sum = sum(curr_acc[a] for a in stay_addrs)
                        total_weight = len(stay_addrs) * 1.0
                        
                        for a in new_addrs:
                            score_a = curr_acc[a]
                            baseline = prev_stay_avg
                            if score_a < baseline:
                                w_a = 0.3
                            else:
                                w_a = 1.0
                            weighted_curr_sum += score_a * w_a
                            total_weight += w_a
                            
                        anti_dilution_curr_avg = (weighted_curr_sum / total_weight) if total_weight > 0 else 0.0
                        prev_avg = sum(prev_acc.values()) / len(prev_acc) if prev_acc else 0.0
                        t.diff_acc_score = round(anti_dilution_curr_avg - prev_avg, 2)
            except Exception as _fe:
                logger.debug("token time-series analysis failed: %s", _fe)

        conn.execute("DETACH src")
    except Exception as _e:
        logger.warning(f"价格补全与时序分析失败: {_e}")

    return list(tokens.values())


def save_meta_result(conn: sqlite3.Connection, result: dict):
    """保存 meta-verdict 仲裁结果 — 含分项积分"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meta_snapshots (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time      TEXT NOT NULL,
            chain          TEXT NOT NULL,
            token_address  TEXT NOT NULL,
            token_symbol   TEXT,
            meta_score     REAL DEFAULT 0,
            meta_score_smooth REAL DEFAULT 0,
            meta_verdict   TEXT DEFAULT 'NEUTRAL',
            engine_hits    INTEGER DEFAULT 0,
            master_signal  TEXT DEFAULT '',
            opus_verdict   TEXT DEFAULT '',
            unified_signal TEXT DEFAULT '',
            whale_level    TEXT DEFAULT '',
            cb_verdict     TEXT DEFAULT '',
            stage          TEXT DEFAULT '',
            confidence_tier TEXT DEFAULT 'L3-Watch',
            resilience_index REAL DEFAULT 0,
            resilience_norm  REAL DEFAULT 0.5,
            master_score   REAL DEFAULT 0,
            opus_score     REAL DEFAULT 0,
            unified_score  REAL DEFAULT 0,
            whale_score    REAL DEFAULT 0,
            cb_score       REAL DEFAULT 0,
            hop2_score     REAL DEFAULT 0,
            UNIQUE(chain, token_address, scan_time)
        )
    """)
    # 防御性 Alter 数据库增加新增字段，防老表崩溃
    for col, typedef in [
        ("meta_score_smooth", "REAL DEFAULT 0"),
        ("resilience_index", "REAL DEFAULT 0"),
        ("resilience_norm", "REAL DEFAULT 0.5"),
        ("confidence_tier", "TEXT DEFAULT 'L3-Watch'")
    ]:
        try:
            conn.execute(f"ALTER TABLE meta_snapshots ADD COLUMN {col} {typedef}")
        except Exception:
            pass
        
    conn.execute("""
        INSERT OR REPLACE INTO meta_snapshots
        (scan_time, chain, token_address, token_symbol, meta_score, meta_score_smooth, meta_verdict,
         engine_hits, master_signal, opus_verdict, unified_signal, whale_level, cb_verdict, stage,
         confidence_tier, resilience_index, resilience_norm,
         master_score, opus_score, unified_score, whale_score, cb_score, hop2_score)
        VALUES (:scan_time, :chain, :token_address, :token_symbol, :meta_score, :meta_score_smooth, :meta_verdict,
                :engine_hits, :master_signal, :opus_verdict, :unified_signal, :whale_level,
                :cb_verdict, :stage,
                :confidence_tier, :resilience_index, :resilience_norm,
                :master_score, :opus_score, :unified_score, :whale_score, :cb_score, :hop2_score)
    """, result)
    conn.commit()


# ── hop2 跟踪采集（v5 新增）──

def ensure_hop2_tracking_table(conn: sqlite3.Connection):
    """确保 hop2_tracking 表存在"""
    conn.execute("""CREATE TABLE IF NOT EXISTS hop2_tracking (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_time       TEXT NOT NULL,
        chain           TEXT NOT NULL DEFAULT 'bsc',
        token_address   TEXT NOT NULL,
        token_symbol    TEXT,
        total_holders   INTEGER DEFAULT 0,
        acc_count       INTEGER DEFAULT 0,
        hop2_high_count INTEGER DEFAULT 0,
        hop2_acc_count  INTEGER DEFAULT 0,
        tier_98_count   INTEGER DEFAULT 0,
        tier_90_count   INTEGER DEFAULT 0,
        tier_30_count   INTEGER DEFAULT 0,
        tier_80_count   INTEGER DEFAULT 0,
        entity_count    INTEGER DEFAULT 0,
        unique_entities INTEGER DEFAULT 0,
        hop2_acc_pct    REAL DEFAULT 0,
        hop2_avg        REAL DEFAULT 0,
        price_usd       REAL DEFAULT 0,
        UNIQUE(token_address, scan_time)
    )""")
    conn.commit()


def collect_hop2_tracking(sum_conn: sqlite3.Connection, scan_time: str, tokens_to_track: list = None):
    """
    每轮 meta-verdict 运行时，从 src.bubblemap_holders 采集 hop2 统计。
    前置条件: src DB 已通过 ATTACH 挂载为 'src'。
    
    字段名对照 bubblemap_holders 表:
      - dex_ratio_hop2  (REAL, nullable)
      - gmgn_verified   (INTEGER, nullable, 0/1/2)
      - entity_id       (TEXT, default '')
      - is_accumulating (INTEGER, default 0)
      - batch_id        (TEXT)
    """
    import config as _cfg
    src_db = os.environ.get("SRC_DB_PATH", _cfg.SRC_DB_PATH)
    
    if tokens_to_track is not None:
        tokens = tokens_to_track
    else:
        tokens = sum_conn.execute(
            "SELECT chain, token_address, token_symbol FROM watchlist"
        ).fetchall()
    
    if not tokens:
        logger.info("[HOP2] watchlist 为空，跳过采集")
        return 0
    
    # ATTACH src DB
    try:
        sum_conn.execute(f"ATTACH '{src_db}' AS src")
    except Exception:
        pass  # 可能已 ATTACH
    
    saved = 0
    for t in tokens:
        chain = t["chain"]
        addr = t["token_address"]
        sym = t["token_symbol"] or "?"
        
        row = sum_conn.execute("""
            SELECT
                COUNT(*)                                                              AS total_holders,
                COALESCE(SUM(is_accumulating), 0)                                     AS acc_count,
                SUM(CASE WHEN dex_ratio_hop2 IS NOT NULL AND dex_ratio_hop2 >= 0.5
                         THEN 1 ELSE 0 END)                                           AS hop2_high_count,
                SUM(CASE WHEN is_accumulating = 1
                         AND dex_ratio_hop2 IS NOT NULL AND dex_ratio_hop2 >= 0.5
                         THEN 1 ELSE 0 END)                                           AS hop2_acc_count,
                SUM(CASE WHEN entity_id IS NOT NULL AND entity_id != ''
                         THEN 1 ELSE 0 END)                                           AS entity_count,
                COUNT(DISTINCT CASE WHEN entity_id IS NOT NULL AND entity_id != ''
                              THEN entity_id END)                                      AS unique_entities,
                SUM(CASE WHEN gmgn_verified = 2 AND dex_ratio_hop2 >= 0.5
                         THEN 1 ELSE 0 END)                                           AS tier_98_count,
                SUM(CASE WHEN gmgn_verified = 1 AND dex_ratio_hop2 >= 0.5
                         THEN 1 ELSE 0 END)                                           AS tier_90_count,
                SUM(CASE WHEN gmgn_verified = 0 AND dex_ratio_hop2 >= 0.5
                         THEN 1 ELSE 0 END)                                           AS tier_30_count,
                SUM(CASE WHEN gmgn_verified IS NULL AND dex_ratio_hop2 >= 0.5
                         THEN 1 ELSE 0 END)                                           AS tier_80_count,
                AVG(CASE WHEN is_accumulating = 1 THEN dex_ratio_hop2 END)            AS hop2_avg
            FROM src.bubblemap_holders
            WHERE token_address = ?
              AND batch_id = (
                  SELECT MAX(batch_id) FROM src.bubblemap_holders WHERE token_address = ?
              )
        """, [addr, addr]).fetchone()
        
        if not row or (row["total_holders"] or 0) == 0:
            continue
        
        acc = row["acc_count"] or 0
        hop2_acc = row["hop2_acc_count"] or 0
        hop2_acc_pct = hop2_acc / max(acc, 1)
        
        # 获取最新价格
        price_row = sum_conn.execute(
            "SELECT price_usd FROM src.gecko_market_data "
            "WHERE token_address = ? AND price_usd > 0 "
            "ORDER BY scan_time DESC LIMIT 1",
            [addr]
        ).fetchone()
        price = price_row[0] if price_row else 0
        
        sum_conn.execute("""
            INSERT OR REPLACE INTO hop2_tracking
            (scan_time, chain, token_address, token_symbol,
             total_holders, acc_count, hop2_high_count, hop2_acc_count,
             tier_98_count, tier_90_count, tier_30_count, tier_80_count,
             entity_count, unique_entities, hop2_acc_pct, hop2_avg, price_usd)
            VALUES (?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?, ?)
        """, [
            scan_time, chain, addr, sym,
            row["total_holders"], acc, row["hop2_high_count"] or 0, hop2_acc,
            row["tier_98_count"] or 0, row["tier_90_count"] or 0,
            row["tier_30_count"] or 0, row["tier_80_count"] or 0,
            row["entity_count"] or 0, row["unique_entities"] or 0,
            round(hop2_acc_pct, 4), round(row["hop2_avg"] or 0, 4), price
        ])
        saved += 1
    
    sum_conn.commit()
    try:
        sum_conn.execute("DETACH src")
    except Exception:
        pass
    logger.info(f"[HOP2] hop2_tracking 写入 {saved}/{len(tokens)} 条")
    return saved
