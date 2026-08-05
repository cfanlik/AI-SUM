import sys
import os
import shutil
import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

# 确保能正确导入项目模块
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from anomaly_watch.generate_live_observation_report import (
    generate_report, parse_dt, format_price
)

# 辅助函数：创建 Mock 数据库结构
def init_mock_databases(tmp_dir, index):
    select_db_path = os.path.join(tmp_dir, f"select_{index}.db")
    val_db_path = os.path.join(tmp_dir, f"signal-validation_{index}.db")
    
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

def setup_paths(tmp_dir, index):
    select_path, val_path = init_mock_databases(tmp_dir, index)
    out_dir = os.path.join(tmp_dir, f"out_{index}")
    latest_md_path = os.path.join(out_dir, "latest_实时信号验证报告_实盘观察.md")
    
    os.environ["SELECT_DB"] = select_path
    os.environ["VALIDATION_DB"] = val_path
    os.environ["OUT_DIR"] = out_dir
    os.environ["LATEST_REPORT_PATH"] = latest_md_path
    
    return select_path, val_path, latest_md_path, out_dir

def test_price_formatting():
    print("Running test_price_formatting...")
    assert format_price(1.2345) == "$1.23"
    assert format_price(0.054321) == "$0.0543"
    assert format_price(0.000123000) == "$0.000123"
    assert format_price(1.2e-11) == "$1.2000e-11"
    print("-> test_price_formatting passed.")

def test_pool_and_time_binding(tmp_dir):
    print("Running test_pool_and_time_binding...")
    select_path, val_path, latest_md_path, _ = setup_paths(tmp_dir, 1)
    as_of = "2026-08-04 07:00:00"
    
    conn_select = sqlite3.connect(select_path)
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool_A", "2026-08-04 01:00:00", 0.5, 10000, 1000, "pancakeswap")
    )
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool_A", "2026-08-04 07:00:00", 0.6, 12000, 1200, "pancakeswap")
    )
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
    conn_val.execute(
        "INSERT INTO asset_identity (chain, token_address, pool_address, token_symbol, a_time, identity_pass, reason_code, event_id, chain_source, chain_confidence) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool_A", "TKN", "2026-08-04 01:00:00", 1, "PASS", "ev_123", "bsc", "high")
    )
    conn_val.commit()
    conn_val.close()
    
    res = generate_report(as_of_arg=as_of, dry_run=False)
    assert res == 0
    
    json_path = latest_md_path.replace(".md", ".json")
    assert os.path.exists(json_path)
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    assert len(data["rows"]) == 1
    row = data["rows"][0]
    assert row["current_price"] == 0.6
    assert row["pool_address"] == "0xpool_A"
    print("-> test_pool_and_time_binding passed.")

def test_holder_missing_null_propagation(tmp_dir):
    print("Running test_holder_missing_null_propagation...")
    select_path, val_path, latest_md_path, _ = setup_paths(tmp_dir, 2)
    as_of = "2026-08-04 07:00:00"
    
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
    assert row["ranked_concentration_delta"] is None
    assert row["cohort_balance_delta"] is None
    assert row["input_status"] == "HOLDER_SNAPSHOT_MISSING"
    assert row["scenario"] == "OBSERVATION_RANGE"
    assert row["prediction"] == "数据不足观望"
    print("-> test_holder_missing_null_propagation passed.")

def test_settlement_window_alignment(tmp_dir):
    print("Running test_settlement_window_alignment...")
    select_path, val_path, latest_md_path, _ = setup_paths(tmp_dir, 3)
    as_of = "2026-08-04 07:00:00"
    
    conn_select = sqlite3.connect(select_path)
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "2026-08-01 01:00:00", 1.0, 10000, 1000, "pancakeswap")
    )
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "2026-08-04 01:30:00", 2.0, 10000, 1000, "pancakeswap")
    )
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "2026-08-04 02:30:00", 3.0, 10000, 1000, "pancakeswap")
    )
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "2026-08-04 06:00:00", 5.0, 10000, 1000, "pancakeswap")
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
    assert row["maturity_status"] == "SETTLED"
    assert row["r3d"] == 1.0
    print("-> test_settlement_window_alignment passed.")

def test_settlement_window_missing(tmp_dir):
    print("Running test_settlement_window_missing...")
    select_path, val_path, latest_md_path, _ = setup_paths(tmp_dir, 4)
    as_of = "2026-08-04 07:00:00"
    
    conn_select = sqlite3.connect(select_path)
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "2026-08-01 01:00:00", 1.0, 10000, 1000, "pancakeswap")
    )
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "2026-08-04 06:00:00", 5.0, 10000, 1000, "pancakeswap")
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
    assert row["maturity_status"] == "EXIT_SNAPSHOT_MISSING"
    assert row["r3d"] is None
    print("-> test_settlement_window_missing passed.")

def test_lp_insufficient_no_interpolation(tmp_dir):
    print("Running test_lp_insufficient_no_interpolation...")
    select_path, val_path, latest_md_path, _ = setup_paths(tmp_dir, 5)
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
    print("-> test_lp_insufficient_no_interpolation passed.")

def test_manifest_gate_lock(tmp_dir):
    print("Running test_manifest_gate_lock...")
    select_path, val_path, latest_md_path, _ = setup_paths(tmp_dir, 6)
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
    print("-> test_manifest_gate_lock passed.")

def test_dry_run_zero_writes(tmp_dir):
    print("Running test_dry_run_zero_writes...")
    select_path, val_path, latest_md_path, _ = setup_paths(tmp_dir, 7)
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
    print("-> test_dry_run_zero_writes passed.")

def test_r7d_settlement_and_whitebox_prediction(tmp_dir):
    print("Running test_r7d_settlement_and_whitebox_prediction...")
    select_path, val_path, latest_md_path, _ = setup_paths(tmp_dir, 8)
    as_of = "2026-08-04 07:00:00"
    
    conn_select = sqlite3.connect(select_path)
    # Entry A: 2026-07-28 01:00:00 (大于 7 天)
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "2026-07-28 01:00:00", 1.0, 10000, 1000, "pancakeswap")
    )
    # 7d 结算窗口内报价 (2026-08-04 01:30:00)
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "2026-08-04 01:30:00", 1.5, 10000, 1000, "pancakeswap")
    )
    # 实时报价 (2026-08-04 07:00:00)
    conn_select.execute(
        "INSERT INTO gecko_market_data VALUES (?,?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xpool", "2026-08-04 07:00:00", 1.5, 10000, 1000, "pancakeswap")
    )
    # 写入 Holder 吸筹
    conn_select.execute(
        "INSERT INTO bubblemap_holders VALUES (?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xholder", "2026-07-28 01:00:00", 10.0, 1, 1)
    )
    conn_select.execute(
        "INSERT INTO bubblemap_holders VALUES (?,?,?,?,?,?,?)",
        ("bsc", "0xtoken", "0xholder", "2026-07-21 01:00:00", 9.0, 1, 1) # 净增 1.0% (吸筹)
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
        ("bsc", "0xtoken", "0xpool", "TKN", "2026-07-28 01:00:00", 1, "PASS", "ev_123", "bsc", "high")
    )
    conn_val.commit()
    conn_val.close()
    
    generate_report(as_of_arg=as_of, dry_run=False)
    
    json_path = latest_md_path.replace(".md", ".json")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    row = data["rows"][0]
    assert row["r7d_maturity_status"] == "SETTLED"
    assert row["r7d"] == 0.5 # 1.5 / 1.0 - 1 = +50.00%
    assert row["prediction"] == "预期拉升 (强吸筹)"
    assert "大户呈净流入" in row["guidance"]
    print("-> test_r7d_settlement_and_whitebox_prediction passed.")

def main():
    print("=================== 启动本地物理闭环测试 ===================")
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        test_price_formatting()
        
        test_pool_and_time_binding(tmp_dir)
        test_holder_missing_null_propagation(tmp_dir)
        test_settlement_window_alignment(tmp_dir)
        test_settlement_window_missing(tmp_dir)
        test_lp_insufficient_no_interpolation(tmp_dir)
        test_manifest_gate_lock(tmp_dir)
        test_dry_run_zero_writes(tmp_dir)
        test_r7d_settlement_and_whitebox_prediction(tmp_dir)
        
    print("=================== 所有本地物理测试 100% 成功 ===================")

if __name__ == "__main__":
    main()
