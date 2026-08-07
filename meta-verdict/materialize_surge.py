import sqlite3
import os
import uuid
import time
from datetime import datetime, timezone, timedelta

PROD_DB = "file:/opt/select-coin/data/select.db?mode=ro"
VAL_DB = "/opt/AI-SUM/data/signal-validation.db"

def init_db(conn):
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS run_manifest (
            run_id TEXT PRIMARY KEY, source_db_path TEXT, as_of_utc TEXT,
            asset_count INTEGER, snapshot_count INTEGER, wallet_row_count INTEGER,
            price_row_count INTEGER, interval_hours REAL, elapsed_sec REAL)
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS snapshot_asset_metrics (
            chain TEXT NOT NULL, token_address TEXT NOT NULL, snapshot_time TEXT NOT NULL,
            symbol TEXT, holder_count INTEGER, acc_count INTEGER, avg_acc_score REAL,
            system_addr_count INTEGER, non_system_count INTEGER,
            PRIMARY KEY (chain, token_address, snapshot_time))
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS snapshot_wallet_membership (
            chain TEXT NOT NULL, token_address TEXT NOT NULL, snapshot_time TEXT NOT NULL,
            wallet_address TEXT NOT NULL, is_accumulating INTEGER, acc_score REAL,
            is_cex INTEGER, is_dex INTEGER, is_contract INTEGER,
            PRIMARY KEY (chain, token_address, snapshot_time, wallet_address))
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS market_pool_asof (
            chain TEXT NOT NULL, token_address TEXT NOT NULL,
            pool_address TEXT, dex_id TEXT, scan_time TEXT NOT NULL, price_usd REAL,
            PRIMARY KEY (chain, token_address, scan_time))
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_sam ON snapshot_asset_metrics(chain, token_address)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_swm ON snapshot_wallet_membership(chain, token_address, snapshot_time)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_mpa ON market_pool_asof(chain, token_address, scan_time)")
    conn.commit()

def run_sync():
    t0 = time.time()
    db_dir = os.path.dirname(VAL_DB)
    os.makedirs(db_dir, exist_ok=True)

    # 自愈：清理历史遗留的 .tmp 临时库（防上次崩溃残留）
    for file in os.listdir(db_dir):
        if file.endswith(".tmp") and "signal-validation.db" in file:
            try:
                os.remove(os.path.join(db_dir, file))
            except Exception:
                pass

    VAL_DB_TMP = VAL_DB + "." + str(uuid.uuid4())[:8] + ".tmp"
    val_conn = None
    src = None
    success = False

    try:
        val_conn = sqlite3.connect(VAL_DB_TMP, timeout=30.0)
        init_db(val_conn)
        src = sqlite3.connect(PROD_DB, uri=True, timeout=30.0)

        # 锁定黄金 6 核心代币白名单（实际映射 8 个合约地址）
        core_symbols = ['TAKE', 'BASED', 'BTW', 'Beat', 'H', 'FOLKS']
        placeholders_core = ",".join(["?"] * len(core_symbols))
        try:
            active_tokens = [r[0] for r in src.execute(
                f"SELECT token_address FROM token_names WHERE symbol IN ({placeholders_core})",
                core_symbols).fetchall() if r[0]]
        except Exception as e:
            print(f"获取核心代币失败: {e}")
            active_tokens = []

        try:
            interval_h = src.execute(
                "SELECT interval_hours FROM bm_schedule_config WHERE enabled=1 LIMIT 1").fetchone()
            interval_hours = interval_h[0] if interval_h else 10.0
        except Exception:
            interval_hours = 10.0

        # 14 天滑动窗口（7d 最长结算周期 × 2 冗余）
        retention_days = 14
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        retention_time = (now_utc - timedelta(days=retention_days)).strftime('%Y-%m-%d %H:%M:%S')

        ts_cnt = tw_cnt = tp_cnt = 0

        if active_tokens:
            ph = ",".join(["?"] * len(active_tokens))
            symbol_rows = src.execute(
                f"SELECT token_address, symbol FROM token_names WHERE token_address IN ({ph})",
                active_tokens).fetchall()
            symbol_map = {r[0]: r[1] for r in symbol_rows if r[0]}

            # SAM（14天过滤）
            sam_rows = src.execute(f"""
                SELECT chain, token_address, snapshot_time,
                       COUNT(*) as hc, SUM(is_accumulating) as ac,
                       ROUND(AVG(CASE WHEN is_accumulating=1 THEN acc_score END), 6) as avg_s,
                       SUM(CASE WHEN is_cex=1 OR is_dex=1 OR is_contract=1 THEN 1 ELSE 0 END) as sys_cnt,
                       SUM(CASE WHEN is_cex=0 AND is_dex=0 AND is_contract=0 THEN 1 ELSE 0 END) as nonsys
                FROM bubblemap_holders
                WHERE token_address IN ({ph}) AND snapshot_time >= ?
                GROUP BY token_address, snapshot_time
            """, active_tokens + [retention_time]).fetchall()

            sam_insert = [[r[0], r[1], r[2], symbol_map.get(r[1]), r[3], r[4], r[5], r[6], r[7]]
                          for r in sam_rows]
            val_conn.executemany(
                "INSERT OR REPLACE INTO snapshot_asset_metrics VALUES (?,?,?,?,?,?,?,?,?)", sam_insert)
            ts_cnt = len(sam_insert)

            # SWM（14天过滤）
            swm_rows = src.execute(f"""
                SELECT chain, token_address, snapshot_time, wallet_address,
                       is_accumulating, acc_score, is_cex, is_dex, is_contract
                FROM bubblemap_holders
                WHERE token_address IN ({ph}) AND snapshot_time >= ?
            """, active_tokens + [retention_time]).fetchall()
            val_conn.executemany(
                "INSERT OR REPLACE INTO snapshot_wallet_membership VALUES (?,?,?,?,?,?,?,?,?)",
                [list(r) for r in swm_rows])
            tw_cnt = len(swm_rows)

            # MPA（14天过滤）
            mpa_rows = src.execute(f"""
                SELECT chain, token_address, pool_address, dex_id, scan_time, price_usd
                FROM gecko_market_data
                WHERE token_address IN ({ph}) AND price_usd > 0 AND scan_time >= ?
            """, active_tokens + [retention_time]).fetchall()
            val_conn.executemany(
                "INSERT OR REPLACE INTO market_pool_asof VALUES (?,?,?,?,?,?)",
                [list(r) for r in mpa_rows])
            tp_cnt = len(mpa_rows)

            val_conn.commit()

        run_id = str(uuid.uuid4())[:8]
        elapsed = round(time.time() - t0, 2)
        val_conn.execute(
            "INSERT OR REPLACE INTO run_manifest VALUES (?,?,?,?,?,?,?,?,?)",
            [run_id, "select.db", now_utc.isoformat(),
             len(active_tokens), ts_cnt, tw_cnt, tp_cnt, interval_hours, elapsed])
        val_conn.commit()
        success = True
        print(f"同步成功 (run_id: {run_id}): {len(active_tokens)} 资产, {ts_cnt} 快照, "
              f"{tw_cnt} 钱包, {tp_cnt} 价格, 耗时 {elapsed}s")

    except Exception as e:
        print("物化同步失败:", e)
    finally:
        if src:
            src.close()
        if val_conn:
            val_conn.close()
        if success:
            os.rename(VAL_DB_TMP, VAL_DB)  # POSIX 原子替换，并发读者零影响
        else:
            if os.path.exists(VAL_DB_TMP):
                try:
                    os.remove(VAL_DB_TMP)
                except Exception:
                    pass

if __name__ == '__main__':
    run_sync()
