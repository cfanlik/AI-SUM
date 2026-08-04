import pytest
import sqlite3
import os
import shutil
import json
from datetime import datetime, timedelta, timezone
from anomaly_watch.generate_live_observation_report import (
    generate_report, parse_dt, format_price
)

# 辅助函数：创建 Mock 数据库结构
def init_mock_databases(tmp_path):
    select_db_path = str(tmp_path / "select.db")
    val_db_path = str(tmp_path / "signal-validation.db")
    
    # 1. 初始化 select.db
    conn_select = sqlite3.connect(select_db_path)
    conn_select.execute("""
        CREATE TABLE gecko_market_data (
            chain TEXT,
            token_address TEXT,
            pool_address TEXT,
            scan_time TEXT,
            price_usd REAL,
            reserve_usd REAL,
            volume_24h REAL,
            dex_id TEXT
        )
    """)
    conn_select.execute("""
        CREATE TABLE bubblemap_holders (
            chain TEXT,
            token_address TEXT,
            wallet_address TEXT,
            snapshot_time TEXT,
            hold_percentage REAL,
            rank INTEGER,
            is_accumulating INTEGER
        )
    """)
    conn_select.close()
    
    # 2. 初始化 signal-validation.db
    conn_val = sqlite3.connect(val_db_path)
    conn_val.execute("""
        CREATE TABLE run_manifest (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            git_commit TEXT,
            schema_version TEXT,
            config_hash TEXT,
            grid_name TEXT,
            status TEXT,
            train_eligible_count INTEGER,
            source_db_mtime TEXT,
            output_table_hash TEXT,
            run_time TEXT
        )
    """)
    conn_val.execute("""
        CREATE TABLE asset_identity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chain TEXT,
            token_address TEXT,
            pool_address TEXT,
            token_symbol TEXT,
            a_time TEXT,
            a_stage TEXT,
            engine_hits INTEGER,
            chain_source TEXT,
            chain_confidence TEXT,
            identity_pass INTEGER,
            reason_code TEXT,
            candidate_pools TEXT,
            identity_conflict INTEGER,
            conflict_time TEXT,
            event_id TEXT
        )
    """)
    conn_val.close()
    
    return select_db_path, val_db_path

@pytest.fixture
def db_paths(tmp_path, monkeypatch):
    select_path, val_path = init_mock_databases(tmp_path)
    out_dir = str(tmp_path / "out")
    latest_md_path = str(tmp_path / "out" / "anomaly_live_observation.md")
    
    monkeypatch.setenv("SELECT_DB", select_path)
    monkeypatch.setenv("VALIDATION_DB", val_path)
    monkeypatch.setenv("OUT_DIR", out_dir)
    monkeypatch.setenv("LATEST_REPORT_PATH", latest_md_path)
    
    yield select_path, val_path, latest_md_path, out_dir

def test_price_formatting():
    # 测试微小代币价格自适应展示规范
    assert format_price(1.2345) == "$1.23"
    assert format_price(0.054321) == "$0.0543"
    assert format_price(0.000123000) == "$0.000123"
    assert format_price(1.2e-11) == "$1.2000e-11"

def test_pool_and_time_binding(db_paths):
    select_path, val_path, latest_md_path, out_dir = db_paths
    as_of = "2026-08-04 07:00:00"
    
    conn_select = sqlite3.connect(select_path)
    # Pool A 行情
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool_A", "2026-08-04 01:00:00", 0.5, 10000, 1000, "pancakeswap")
    )
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool_A", "2026-08-04 07:00:00", 0.6, 12000, 1200, "pancakeswap")
    )
    # Pool B 行情 (价格异常高，用以验证是否被隔离)
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool_B", "2026-08-04 07:00:00", 99.9, 50000, 5000, "pancakeswap")
    )
    conn_select.commit()
    conn_select.close()
    
    conn_val = sqlite3.connect(val_path)
    conn_val.execute(
        "INSERT INTO run_manifest (git_commit, schema_version, config_hash, grid_name, status, train_eligible_count, source_db_mtime, output_table_hash, run_time) VALUES (?,?,?,?,?,?,?,?,?)",
        ("git_commit", "v6", "cfg_hash", "grid", "SUCCESS", 10, "mtime", "outhash", "2026-08-04 06:00:00")
    )
    # 事件绑定在 Pool A
    conn_val.execute(
        "INSERT INTO asset_identity (chain, token_address, pool_address, token_symbol, a_time, identity_pass, reason_code, event_id, chain_source, chain_confidence) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool_A", "TKN", "2026-08-04 01:00:00", 1, "PASS", "ev_123", "bsc", "high")
    )
    conn_val.commit()
    conn_val.close()
    
    res = generate_report(as_of_arg=as_of, dry_run=False)
    assert res == 0
    
    # 检验生成的 JSON
    json_path = latest_md_path.replace(".md", ".json")
    assert os.path.exists(json_path)
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    assert len(data["rows"]) == 1
    row = data["rows"][0]
    # 断言：当前价读的是 pool A 的 $0.6，而不是 pool B 的 $99.9
    assert row["current_price"] == 0.6
    assert row["pool_address"] == "0xpool_A"

def test_holder_missing_null_propagation(db_paths):
    select_path, val_path, latest_md_path, _ = db_paths
    as_of = "2026-08-04 07:00:00"
    
    # AEON 案例：没有快照数据
    conn_select = sqlite3.connect(select_path)
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xaeon", "0xpool_aeon", "2026-08-04 01:00:00", 0.1, 10000, 1000, "pancakeswap")
    )
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xaeon", "0xpool_aeon", "2026-08-04 07:00:00", 0.2, 10000, 1000, "pancakeswap")
    )
    conn_select.commit()
    conn_select.close()
    
    conn_val = sqlite3.connect(val_path)
    conn_val.execute(
        "INSERT INTO run_manifest (git_commit, schema_version, config_hash, grid_name, status, train_eligible_count, source_db_mtime, output_table_hash, run_time) VALUES (?,?,?,?,?,?,?,?,?)",
        ("git_commit", "v6", "cfg_hash", "grid", "SUCCESS", 10, "mtime", "outhash", "2026-08-04 06:00:00")
    )
    conn_val.execute(
        "INSERT INTO asset_identity (chain, token_address, pool_address, token_symbol, a_time, identity_pass, reason_code, event_id, chain_source, chain_confidence) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("bsc", "0xaeon", "0xpool_aeon", "AEON", "2026-08-04 01:00:00", 1, "PASS", "ev_aeon", "bsc", "high")
    )
    conn_val.commit()
    conn_val.close()
    
    generate_report(as_of_arg=as_of, dry_run=False)
    
    json_path = latest_md_path.replace(".md", ".json")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    row = data["rows"][0]
    # 断言：Holder 快照缺失时，其 delta 与 ratio 必须为 None
    assert row["ranked_concentration_delta"] is None
    assert row["cohort_balance_delta"] is None
    assert row["input_status"] == "HOLDER_SNAPSHOT_MISSING"
    # 并且决策为中性（未突破）
    assert "UP_MOVE" not in row["scenario"]
    assert "DOWN_MOVE" not in row["scenario"]

def test_settlement_window_alignment(db_paths):
    select_path, val_path, latest_md_path, _ = db_paths
    # 信号发生于 08-01 01:00:00，结算基线在 08-04 07:00:00（满 3d 且超过窗口）
    as_of = "2026-08-04 07:00:00"
    
    conn_select = sqlite3.connect(select_path)
    # Entry quote
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "2026-08-01 01:00:00", 1.0, 10000, 1000, "pancakeswap")
    )
    # 模拟 [A+3d, A+3d+4h] 窗口内的报价：08-04 01:00:00 到 08-04 05:00:00
    # 我们插入两条窗口内报价：01:30 的 $2.0 和 02:30 的 $3.0
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "2026-08-04 01:30:00", 2.0, 10000, 1000, "pancakeswap")
    )
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "2026-08-04 02:30:00", 3.0, 10000, 1000, "pancakeswap")
    )
    # 插入窗口外的报价 (08-04 06:00:00 的 $5.0)，用以验证是否被排除
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "2026-08-04 06:00:00", 5.0, 10000, 1000, "pancakeswap")
    )
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "2026-08-04 07:00:00", 5.0, 10000, 1000, "pancakeswap")
    )
    conn_select.commit()
    conn_select.close()
    
    conn_val = sqlite3.connect(val_path)
    conn_val.execute(
        "INSERT INTO run_manifest (git_commit, schema_version, config_hash, grid_name, status, train_eligible_count, source_db_mtime, output_table_hash, run_time) VALUES (?,?,?,?,?,?,?,?,?)",
        ("git_commit", "v6", "cfg_hash", "grid", "SUCCESS", 10, "mtime", "outhash", "2026-08-04 06:00:00")
    )
    conn_val.execute(
        "INSERT INTO asset_identity (chain, token_address, pool_address, token_symbol, a_time, identity_pass, reason_code, event_id, chain_source, chain_confidence) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "TKN", "2026-08-01 01:00:00", 1, "PASS", "ev_123", "bsc", "high")
    )
    conn_val.commit()
    conn_val.close()
    
    generate_report(as_of_arg=as_of, dry_run=False)
    
    json_path = latest_md_path.replace(".md", ".json")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    row = data["rows"][0]
    # 断言：r3d 取的是窗口内的最早一条价格 ($2.0)，即收益率 (2.0 / 1.0 - 1) = +100.00%
    # 而不是窗口外 06:00:00 的 $5.0 价格
    assert row["maturity_status"] == "SETTLED"
    assert row["r3d"] == 1.0

def test_settlement_window_missing(db_paths):
    select_path, val_path, latest_md_path, _ = db_paths
    as_of = "2026-08-04 07:00:00"
    
    conn_select = sqlite3.connect(select_path)
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "2026-08-01 01:00:00", 1.0, 10000, 1000, "pancakeswap")
    )
    # 窗口内无报价，但在窗口外 06:00:00 有报价
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "2026-08-04 06:00:00", 5.0, 10000, 1000, "pancakeswap")
    )
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "2026-08-04 07:00:00", 5.0, 10000, 1000, "pancakeswap")
    )
    conn_select.commit()
    conn_select.close()
    
    conn_val = sqlite3.connect(val_path)
    conn_val.execute(
        "INSERT INTO run_manifest (git_commit, schema_version, config_hash, grid_name, status, train_eligible_count, source_db_mtime, output_table_hash, run_time) VALUES (?,?,?,?,?,?,?,?,?)",
        ("git_commit", "v6", "cfg_hash", "grid", "SUCCESS", 10, "mtime", "outhash", "2026-08-04 06:00:00")
    )
    conn_val.execute(
        "INSERT INTO asset_identity (chain, token_address, pool_address, token_symbol, a_time, identity_pass, reason_code, event_id, chain_source, chain_confidence) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "TKN", "2026-08-01 01:00:00", 1, "PASS", "ev_123", "bsc", "high")
    )
    conn_val.commit()
    conn_val.close()
    
    generate_report(as_of_arg=as_of, dry_run=False)
    
    json_path = latest_md_path.replace(".md", ".json")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    row = data["rows"][0]
    # 断言：因为窗口内无报价，结算状态被标记为 EXIT_SNAPSHOT_MISSING，r3d 为 None
    assert row["maturity_status"] == "EXIT_SNAPSHOT_MISSING"
    assert row["r3d"] is None

def test_lp_insufficient_no_interpolation(db_paths):
    select_path, val_path, latest_md_path, _ = db_paths
    as_of = "2026-08-04 07:00:00"
    
    # 7d 内 LP 数据少于 5 条 (仅有 2 条)
    conn_select = sqlite3.connect(select_path)
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "2026-08-04 01:00:00", 1.0, 10000, 1000, "pancakeswap")
    )
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "2026-08-04 07:00:00", 1.0, 10000, 1000, "pancakeswap")
    )
    conn_select.execute(
        "INSERT INTO bubblemap_holders VALUES (?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xholder", "2026-08-04 01:00:00", 10.0, 1, 1)
    )
    conn_select.execute(
        "INSERT INTO bubblemap_holders VALUES (?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xholder", "2026-07-28 01:00:00", 10.0, 1, 1)
    )
    conn_select.commit()
    conn_select.close()
    
    conn_val = sqlite3.connect(val_path)
    conn_val.execute(
        "INSERT INTO run_manifest (git_commit, schema_version, config_hash, grid_name, status, train_eligible_count, source_db_mtime, output_table_hash, run_time) VALUES (?,?,?,?,?,?,?,?,?)",
        ("git_commit", "v6", "cfg_hash", "grid", "SUCCESS", 10, "mtime", "outhash", "2026-08-04 06:00:00")
    )
    conn_val.execute(
        "INSERT INTO asset_identity (chain, token_address, pool_address, token_symbol, a_time, identity_pass, reason_code, event_id, chain_source, chain_confidence) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "TKN", "2026-08-04 01:00:00", 1, "PASS", "ev_123", "bsc", "high")
    )
    conn_val.commit()
    conn_val.close()
    
    generate_report(as_of_arg=as_of, dry_run=False)
    
    json_path = latest_md_path.replace(".md", ".json")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    row = data["rows"][0]
    assert row["lp_drawdown"] is False
    assert row["input_status"] == "LP_BASELINE_INSUFFICIENT"

def test_manifest_gate_lock(db_paths):
    select_path, val_path, latest_md_path, _ = db_paths
    as_of = "2026-08-04 07:00:00"
    
    conn_select = sqlite3.connect(select_path)
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "2026-08-04 01:00:00", 1.0, 10000, 1000, "pancakeswap")
    )
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "2026-08-04 07:00:00", 1.0, 10000, 1000, "pancakeswap")
    )
    conn_select.commit()
    conn_select.close()
    
    conn_val = sqlite3.connect(val_path)
    conn_val.execute(
        "INSERT INTO run_manifest (git_commit, schema_version, config_hash, grid_name, status, train_eligible_count, source_db_mtime, output_table_hash, run_time) VALUES (?,?,?,?,?,?,?,?,?)",
        ("git_commit", "v6", "cfg_hash", "grid", "SUCCESS_DEGRADED", 0, "mtime", "outhash", "2026-08-04 06:00:00")
    )
    conn_val.execute(
        "INSERT INTO asset_identity (chain, token_address, pool_address, token_symbol, a_time, identity_pass, reason_code, event_id, chain_source, chain_confidence) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "TKN", "2026-08-04 01:00:00", 1, "PASS", "ev_123", "bsc", "high")
    )
    conn_val.commit()
    conn_val.close()
    
    generate_report(as_of_arg=as_of, dry_run=False)
    
    json_path = latest_md_path.replace(".md", ".json")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    assert data["report_metadata"]["evaluation_status"] == "NOT_EVALUATED"
    assert data["report_metadata"]["evaluation_reason"] == "INSUFFICIENT_TRAINING_SAMPLE"
    assert data["rows"][0]["gate_decision"] == "INTERCEPTED"

def test_dry_run_zero_writes(db_paths):
    select_path, val_path, latest_md_path, out_dir = db_paths
    as_of = "2026-08-04 07:00:00"
    
    conn_select = sqlite3.connect(select_path)
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "2026-08-04 01:00:00", 1.0, 10000, 1000, "pancakeswap")
    )
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "2026-08-04 07:00:00", 1.0, 10000, 1000, "pancakeswap")
    )
    conn_select.commit()
    conn_select.close()
    
    conn_val = sqlite3.connect(val_path)
    conn_val.execute(
        "INSERT INTO run_manifest (git_commit, schema_version, config_hash, grid_name, status, train_eligible_count, source_db_mtime, output_table_hash, run_time) VALUES (?,?,?,?,?,?,?,?,?)",
        ("git_commit", "v6", "cfg_hash", "grid", "SUCCESS", 10, "mtime", "outhash", "2026-08-04 06:00:00")
    )
    conn_val.execute(
        "INSERT INTO asset_identity (chain, token_address, pool_address, token_symbol, a_time, identity_pass, reason_code, event_id, chain_source, chain_confidence) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "TKN", "2026-08-04 01:00:00", 1, "PASS", "ev_123", "bsc", "high")
    )
    conn_val.commit()
    conn_val.close()
    
    res = generate_report(as_of_arg=as_of, dry_run=True)
    assert res == 0
    assert not os.path.exists(latest_md_path)
    assert not os.path.exists(latest_md_path.replace(".md", ".json"))
