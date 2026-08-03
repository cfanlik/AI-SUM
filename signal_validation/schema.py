import sqlite3

def init_db(db_path: str = '/opt/AI-SUM/data/signal-validation.db'):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    # 1. 资产身份表 (P0-P1)
    c.execute('''
        CREATE TABLE IF NOT EXISTS asset_identity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chain TEXT NOT NULL,
            token_address TEXT NOT NULL,
            pool_address TEXT,
            token_symbol TEXT,
            a_time TEXT NOT NULL,
            a_stage TEXT NOT NULL,
            engine_hits INTEGER,
            chain_source TEXT NOT NULL,
            chain_confidence TEXT NOT NULL,
            identity_pass INTEGER NOT NULL, -- 1=PASS, 0=DENIED
            reason_code TEXT NOT NULL,
            candidate_pools TEXT,
            identity_conflict INTEGER DEFAULT 0,
            conflict_time TEXT,
            event_id TEXT
        )
    ''')
    
    try:
        c.execute('ALTER TABLE asset_identity ADD COLUMN identity_conflict INTEGER DEFAULT 0')
    except sqlite3.OperationalError:
        pass
    try:
        c.execute('ALTER TABLE asset_identity ADD COLUMN conflict_time TEXT')
    except sqlite3.OperationalError:
        pass
    try:
        c.execute('ALTER TABLE asset_identity ADD COLUMN event_id TEXT')
    except sqlite3.OperationalError:
        pass
    
    # 2. 自然日特征快照表 (P0-P2)
    c.execute('''
        CREATE TABLE IF NOT EXISTS daily_feature_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chain TEXT NOT NULL,
            token_address TEXT NOT NULL,
            pool_address TEXT NOT NULL,
            as_of_date TEXT NOT NULL,
            source_scan_time TEXT NOT NULL,
            price_usd REAL NOT NULL,
            reserve_usd REAL NOT NULL,
            daily_24h_rolling_volume_proxy REAL NOT NULL,
            dex_id TEXT
        )
    ''')

    # 3. 回测运行历史表 (P4)
    c.execute('''
        CREATE TABLE IF NOT EXISTS backtest_run_history (
            config_hash TEXT PRIMARY KEY,
            grid_name TEXT NOT NULL,
            v2_mul REAL NOT NULL,
            v3_mul REAL NOT NULL,
            breakout REAL NOT NULL,
            status TEXT NOT NULL,
            train_eligible_count INTEGER NOT NULL,
            run_time TEXT NOT NULL
        )
    ''')

    # 4. 信号决策结果表 (P3-P4)
    c.execute('''
        CREATE TABLE IF NOT EXISTS signal_decision_result (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chain TEXT NOT NULL,
            token_address TEXT NOT NULL,
            pool_address TEXT NOT NULL,
            symbol TEXT,
            a_time TEXT NOT NULL,
            path_label TEXT NOT NULL, -- B_triggered / I_invalidated / censored
            path_time TEXT,
            label_end_time TEXT NOT NULL,
            reason TEXT NOT NULL,
            r1d REAL,
            r3d REAL,
            r7d REAL,
            mdd_7d REAL,
            outcome_incomplete TEXT NOT NULL DEFAULT 'PASS',
            soft_risk TEXT NOT NULL, -- PASS / Top10_hold_net_variation_alert
            split_type TEXT NOT NULL, -- Train / Time_Holdout
            overlap_check TEXT NOT NULL -- PASS / OVERLAP_VIOLATION
        )
    ''')
    
    try:
        c.execute("ALTER TABLE signal_decision_result ADD COLUMN outcome_incomplete TEXT NOT NULL DEFAULT 'PASS'")
    except sqlite3.OperationalError:
        pass
        
    # 5. API 风控核验审计日志表 (C6)
    c.execute('''
        CREATE TABLE IF NOT EXISTS api_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            as_of_utc TEXT NOT NULL,
            source_name TEXT NOT NULL,
            request_url TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            price REAL,
            delay REAL,
            deviation REAL,
            error_code TEXT
        )
    ''')
    
    # 6. 可审计 Run Manifest 运行清单表 (C7)
    c.execute('''
        CREATE TABLE IF NOT EXISTS run_manifest (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            git_commit TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            config_hash TEXT NOT NULL,
            grid_name TEXT NOT NULL,
            status TEXT NOT NULL,
            train_eligible_count INTEGER NOT NULL,
            source_db_mtime TEXT NOT NULL,
            output_table_hash TEXT NOT NULL,
            run_time TEXT NOT NULL
        )
    ''')
    
    conn.commit()
    conn.close()
    print(f"数据库 {db_path} 结构（含 C1-C8 迁移兼容）初始化完毕。")

