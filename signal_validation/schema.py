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
            candidate_pools TEXT
        )
    ''')
    
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
    
    conn.commit()
    conn.close()
    print(f"数据库 {db_path} 结构初始化完毕。")
