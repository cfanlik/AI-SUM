import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta

from anomaly_watch.accumulation_top10_analyzer import AccumulationTop10Analyzer
from anomaly_watch.report_generator import AnomalyReportGenerator


class AccumulationTop10AnalyzerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.select_db = os.path.join(self.tmp.name, "select.db")
        self.sum_db = os.path.join(self.tmp.name, "sum.db")
        self._build_select_db()
        self._build_sum_db()

    def tearDown(self):
        self.tmp.cleanup()

    def _build_select_db(self):
        conn = sqlite3.connect(self.select_db)
        conn.executescript("""
            CREATE TABLE token_names (
                chain TEXT, token_address TEXT, symbol TEXT,
                bm_last_snapshot TEXT, bm_acc_count INTEGER
            );
            CREATE TABLE bubblemap_holders (
                chain TEXT, token_address TEXT, snapshot_time TEXT,
                wallet_address TEXT, hold_amount REAL, is_accumulating INTEGER
            );
            CREATE TABLE gecko_market_data (
                chain TEXT, token_address TEXT, scan_time TEXT,
                reserve_usd REAL, volume_24h REAL
            );
        """)
        today = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
        for index, (symbol, days, base) in enumerate((
            ("FAST", 15, 100.0),
            ("SLOW", 15, 1000.0),
            ("SHORT", 5, 100.0),
        )):
            address = f"0x{index + 1:040x}"
            latest = today.strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("INSERT INTO token_names VALUES (?,?,?,?,?)", ("bsc", address, symbol, latest, 2))
            conn.execute("INSERT INTO gecko_market_data VALUES (?,?,?,?,?)", ("bsc", address, latest, 50000, 10000))
            for day in range(days):
                stamp = (today - timedelta(days=days - day - 1)).strftime("%Y-%m-%d %H:%M:%S")
                for wallet_no in range(2):
                    conn.execute(
                        "INSERT INTO bubblemap_holders VALUES (?,?,?,?,?,?)",
                        ("bsc", address, stamp, f"w{wallet_no}", base + day * 10 + wallet_no, 1 if day == days - 1 else 0),
                    )
        # 同一 chain/address 的重复名称行不能造成重复候选。
        conn.execute(
            "INSERT INTO token_names VALUES (?,?,?,?,?)",
            ("bsc", "0x0000000000000000000000000000000000000001", "FAST", today.strftime("%Y-%m-%d %H:%M:%S"), 2),
        )
        conn.commit()
        conn.close()

    def _build_sum_db(self):
        conn = sqlite3.connect(self.sum_db)
        conn.executescript("""
            CREATE TABLE token_history (
                computed_date TEXT, chain TEXT, token_address TEXT,
                pnl_ratio REAL, price_now_ret REAL, volume_24h REAL, reserve_usd REAL
            );
            CREATE TABLE meta_snapshots (
                scan_time TEXT, chain TEXT, token_address TEXT,
                meta_score REAL, stage TEXT
            );
        """)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for index in range(3):
            address = f"0x{index + 1:040x}"
            # 历史旧数据的 chain 为空，分析器仍须按地址兼容，但不能按 symbol 查询。
            conn.execute("INSERT INTO token_history VALUES (?,?,?,?,?,?,?)", (now[:10], "", address, 5, 3, 10000, 50000))
            conn.execute("INSERT INTO meta_snapshots VALUES (?,?,?,?,?)", (now, "bsc", address, 4.0, "ACCUMULATING"))
        conn.commit()
        conn.close()

    def test_global_gate_rank_persist_and_report_context(self):
        analyzer = AccumulationTop10Analyzer(
            self.select_db,
            self.sum_db,
            result_db_path=self.sum_db,
            min_effective_days=14,
        )
        run = analyzer.analyze(persist=True, run_id="fixture-run")
        self.assertEqual(run.candidate_count, 3)
        self.assertEqual(run.quality_pass_count, 2)
        self.assertEqual([row.token_symbol for row in run.top10], ["FAST", "SLOW"])
        self.assertIn("LOW_COVERAGE", run.risk_rows[0].risk_flags)

        with sqlite3.connect(self.sum_db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM impulse_accumulation_snapshots").fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM impulse_accumulation_series WHERE run_id='fixture-run'").fetchone()[0], 35)

        generator = AnomalyReportGenerator()
        generator.output_dir = self.tmp.name
        report_path = generator.generate_periodic_impulse_surge_report(run)
        with open(report_path, encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn('data-run-id="fixture-run"', content)
        self.assertIn('data-chain="bsc"', content)
        self.assertIn('data-token-address="0x0000000000000000000000000000000000000001"', content)


if __name__ == "__main__":
    unittest.main()
