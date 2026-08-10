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
    import shutil
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
    
    # 克隆现有的隔离库，以实现增量累积与原子替换，避免每次全量拉取
    db_exists = os.path.exists(VAL_DB) and os.path.getsize(VAL_DB) > 0
    if db_exists:
        try:
            shutil.copy2(VAL_DB, VAL_DB_TMP)
            print("已克隆现有隔离库进行增量同步")
        except Exception as e:
            print(f"克隆隔离库失败，将进行全新物化: {e}")
            db_exists = False

    val_conn = None
    src = None
    success = False

    try:
        val_conn = sqlite3.connect(VAL_DB_TMP, timeout=30.0)
        if not db_exists:
            init_db(val_conn)
            
        src = sqlite3.connect(PROD_DB, uri=True, timeout=30.0)

        try:
            interval_h = src.execute(
                "SELECT interval_hours FROM bm_schedule_config WHERE enabled=1 LIMIT 1").fetchone()
            interval_hours = interval_h[0] if interval_h else 10.0
        except Exception:
            interval_hours = 10.0

        # 14 天滑动窗口（7d 最长结算周期 × 2 冗余）
        retention_days = 14
        now_utc = datetime.now()  # 改为本地北京时间 (HKT)，以对齐 select.db 本地时间戳
        retention_time = (now_utc - timedelta(days=retention_days)).strftime('%Y-%m-%d %H:%M:%S')

        # 1. 物理清理 14 天前的隔离库过期历史数据
        val_conn.execute("DELETE FROM snapshot_asset_metrics WHERE snapshot_time < ?", [retention_time])
        val_conn.execute("DELETE FROM snapshot_wallet_membership WHERE snapshot_time < ?", [retention_time])
        val_conn.execute("DELETE FROM market_pool_asof WHERE scan_time < ?", [retention_time])
        val_conn.commit()

        # 2. 判断隔离库是否为空 (决定是否触发分批 Bootstrap 补全 14 天)
        try:
            stored_cnt = val_conn.execute("SELECT COUNT(*) FROM snapshot_asset_metrics").fetchone()[0]
        except Exception:
            stored_cnt = 0

        bootstrap_mode = (stored_cnt == 0)
        
        if bootstrap_mode:
            print("隔离库数据为空，启动 14 天历史分批 Bootstrap 导入...")
            # 将 14 天划分为 7 个批次，每批 2 天，分批提取和聚合以规避单次大事务超时
            batches = []
            for i in range(7):
                b_start = (now_utc - timedelta(days=14 - i * 2)).strftime('%Y-%m-%d %H:%M:%S')
                b_end = (now_utc - timedelta(days=14 - (i + 1) * 2)).strftime('%Y-%m-%d %H:%M:%S')
                batches.append((b_start, b_end))
        else:
            # 增量模式：只拉取最近 2 天的数据进行同步 (毫秒级极速跑完)
            sync_start = (now_utc - timedelta(days=2)).strftime('%Y-%m-%d %H:%M:%S')
            batches = [(sync_start, now_utc.strftime('%Y-%m-%d %H:%M:%S'))]

        ts_cnt = tw_cnt = tp_cnt = 0
        total_active_tokens = set()

        for idx, (t_start, t_end) in enumerate(batches):
            t_batch_start = time.time()
            
            # 获取该时间段内有快照的代币地址
            active_tokens = [r[0] for r in src.execute(
                "SELECT DISTINCT token_address FROM bubblemap_holders WHERE snapshot_time >= ? AND snapshot_time < ?",
                [t_start, t_end]).fetchall() if r[0]]
            
            if not active_tokens:
                continue

            total_active_tokens.update(active_tokens)
            ph = ",".join(["?"] * len(active_tokens))
            symbol_rows = src.execute(
                f"SELECT token_address, symbol FROM token_names WHERE token_address IN ({ph})",
                active_tokens).fetchall()
            symbol_map = {r[0]: r[1] for r in symbol_rows if r[0]}

            # SAM 聚合导入
            sam_rows = src.execute(f"""
                SELECT chain, token_address, snapshot_time,
                       COUNT(*) as hc, SUM(is_accumulating) as ac,
                       ROUND(AVG(CASE WHEN is_accumulating=1 THEN acc_score END), 6) as avg_s,
                       SUM(CASE WHEN is_cex=1 OR is_dex=1 OR is_contract=1 THEN 1 ELSE 0 END) as sys_cnt,
                       SUM(CASE WHEN is_cex=0 AND is_dex=0 AND is_contract=0 THEN 1 ELSE 0 END) as nonsys
                FROM bubblemap_holders
                WHERE token_address IN ({ph}) AND snapshot_time >= ? AND snapshot_time < ?
                GROUP BY token_address, snapshot_time
            """, active_tokens + [t_start, t_end]).fetchall()

            sam_insert = [[r[0], r[1], r[2], symbol_map.get(r[1]), r[3], r[4], r[5], r[6], r[7]]
                          for r in sam_rows]
            val_conn.executemany(
                "INSERT OR REPLACE INTO snapshot_asset_metrics VALUES (?,?,?,?,?,?,?,?,?)", sam_insert)
            ts_cnt += len(sam_insert)

            # MPA 行情价格导入
            mpa_rows = src.execute(f"""
                SELECT chain, token_address, pool_address, dex_id, scan_time, price_usd
                FROM gecko_market_data
                WHERE token_address IN ({ph}) AND price_usd > 0 AND scan_time >= ? AND scan_time < ?
            """, active_tokens + [t_start, t_end]).fetchall()

            val_conn.executemany(
                "INSERT OR REPLACE INTO market_pool_asof VALUES (?,?,?,?,?,?)",
                [list(r) for r in mpa_rows])
            tp_cnt += len(mpa_rows)

            val_conn.commit()
            t_batch_elapsed = round(time.time() - t_batch_start, 2)
            print(f"  [Batch {idx+1}/{len(batches)}] {t_start} 至 {t_end} 同步完成: {len(active_tokens)} 资产, {len(sam_rows)} 快照, 耗时 {t_batch_elapsed}s")

        run_id = str(uuid.uuid4())[:8]
        elapsed = round(time.time() - t0, 2)
        val_conn.execute(
            "INSERT OR REPLACE INTO run_manifest VALUES (?,?,?,?,?,?,?,?,?)",
            [run_id, "select.db", now_utc.isoformat(),
             len(total_active_tokens), ts_cnt, tw_cnt, tp_cnt, interval_hours, elapsed])
        val_conn.commit()
        success = True
        print(f"同步成功 (run_id: {run_id}): {len(total_active_tokens)} 资产, {ts_cnt} 快照, "
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
