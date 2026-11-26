import importlib.util
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "meta-verdict") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "meta-verdict"))

from alpha_oi_scan.alpha_filter import evaluate_alpha_token
from history_report import l3_gate_status
from position_handover import scan_position_handovers


class TestCrossProjectFixes(unittest.TestCase):
    def test_alpha_filter_no_999_when_mcap_missing(self):
        res = evaluate_alpha_token({
            "symbol": "MOVE",
            "token_address": "0x123",
            "chain": "BSC",
            "total_oi_usd": 12_000_000.0,
            "market_cap_usd": 0.0,
            "oi_change_24h": 0.10,
            "top20_ratio": 0.5,
            "gain_90d_ratio": 1.5,
            "price_now": 0.5,
        })
        self.assertEqual(res["oi_mcap_ratio"], 0.0)
        self.assertEqual(res["stage"], "─ L4-观察/未达标")
        self.assertIn("缺失市值数据", res["risk_flags"])

    def test_handover_with_and_without_cost_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "sel.db")
            conn = sqlite3.connect(db)
            conn.execute("""
                CREATE TABLE bubblemap_holders (
                    chain TEXT, token_address TEXT, snapshot_time TEXT,
                    wallet_address TEXT, hold_amount REAL, hold_percentage REAL,
                    is_cex INTEGER, is_contract INTEGER, gmgn_buy_cost_usd REAL
                )
            """)
            conn.execute("CREATE TABLE gecko_market_data (token_address TEXT, price_usd REAL, scan_time TEXT)")
            conn.execute("INSERT INTO gecko_market_data VALUES ('0xabc', 1.0, '2026-11-26 08:00:00')")
            conn.execute("INSERT INTO bubblemap_holders VALUES ('bsc', '0xabc', '2026-10-20 08:00:00', '0xold', 20000, 5.0, 0, 0, 15000)")
            conn.execute("INSERT INTO bubblemap_holders VALUES ('bsc', '0xabc', '2026-11-26 08:00:00', '0xnew', 20000, 5.0, 0, 0, 21000)")
            conn.commit()
            res = scan_position_handovers(conn, [{"chain": "bsc", "token_address": "0xabc", "token_symbol": "TKN"}])
            self.assertIn("bsc_0xabc", res)
            self.assertEqual(res["bsc_0xabc"]["handover"]["cost_usd"], 21000.0)
            conn.close()

    def test_l3_gate_status_real_run_manifest_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            vdb = os.path.join(tmp, "val.db")
            conn = sqlite3.connect(vdb)
            conn.execute("CREATE TABLE run_manifest (run_id TEXT PRIMARY KEY, source_db_path TEXT, as_of_utc TEXT)")
            conn.execute("CREATE TABLE backtest_run_history (config_hash TEXT PRIMARY KEY, grid_name TEXT, status TEXT, run_time TEXT)")
            conn.execute("CREATE TABLE signal_decision_result (id INTEGER PRIMARY KEY, chain TEXT, token_address TEXT, pool_address TEXT, path_label TEXT, split_type TEXT)")
            conn.execute("CREATE TABLE asset_identity (id INTEGER PRIMARY KEY, reason_code TEXT, identity_pass INTEGER, identity_conflict INTEGER)")
            conn.execute("INSERT INTO run_manifest VALUES ('r1', '/opt/select.db', '2026-11-26T00:00:00Z')")
            conn.commit()
            conn.close()
            out = l3_gate_status(vdb)
            self.assertIn("DENIED", out)
            self.assertNotIn("no such column", out)

    def test_unified_sum_db_default_paths(self):
        for cfg_rel in ("master-scan/config.py", "opus-scan/config.py", "unified-scan/config.py", "python-ai/config.py", "cost-basis-scan/config.py"):
            content = (REPO_ROOT / cfg_rel).read_text(encoding="utf-8")
            self.assertNotIn("data/select-sum.db", content, msg=cfg_rel)
            self.assertNotIn('"data" / "select-sum.db"', content, msg=cfg_rel)


if __name__ == "__main__":
    unittest.main()
