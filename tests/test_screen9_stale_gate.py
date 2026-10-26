#!/usr/bin/env python3
"""
test_screen9_stale_gate.py — 验证第九屏 CEX 时序新鲜度门禁与第一屏文案解耦
"""
import unittest
import sqlite3
import os
import sys
from datetime import datetime, timedelta

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(CURRENT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, "meta-verdict"))

import cex_divergence_analyzer
import report_generator
from arbitrator import MetaResult


class TestScreen9StaleGate(unittest.TestCase):

    def setUp(self):
        self.test_db = "/tmp/test_cex_stale.db"
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
        conn = sqlite3.connect(self.test_db)
        conn.executescript("""
            CREATE TABLE cex_flow_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_date TEXT NOT NULL,
                category TEXT NOT NULL,
                item_name TEXT NOT NULL,
                inflow_usd REAL NOT NULL,
                outflow_usd REAL NOT NULL,
                net_flow_usd REAL NOT NULL,
                tx_count INTEGER,
                ticket_avg_usd REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # 写入 48 小时前的过期数据
        stale_date_1 = (datetime.now() - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
        stale_date_2 = (datetime.now() - timedelta(hours=56)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO cex_flow_snapshots (batch_date, category, item_name, inflow_usd, outflow_usd, net_flow_usd) VALUES (?, 'TOKEN', 'BTC', 100, 200, -100)", (stale_date_1,))
        conn.execute("INSERT INTO cex_flow_snapshots (batch_date, category, item_name, inflow_usd, outflow_usd, net_flow_usd) VALUES (?, 'TOKEN', 'BTC', 200, 100, 100)", (stale_date_2,))
        conn.commit()
        conn.close()

        # monkey-patch 数据库路径
        cex_divergence_analyzer.SRC_DB_PATH = self.test_db

    def tearDown(self):
        if os.path.exists(self.test_db):
            os.remove(self.test_db)

    def test_01_screen9_stale_detection(self):
        res = cex_divergence_analyzer.analyze_cex_flow_divergence()
        self.assertEqual(res["stats"].get("status"), "SOURCE_STALE")
        self.assertEqual(len(res["top_alerts"]), 0)
        self.assertIn("过期熔断", res["stats"].get("message", ""))

    def test_02_report_generator_stale_placeholder(self):
        md = report_generator.generate_report(
            acc_list=[],
            dist_list=[],
            all_count=0,
            scan_time="2026-10-26 15:00:00",
            all_arbitrated=[]
        )
        self.assertIn("数据源时效熔断", md)
        self.assertNotIn("突发大额充值流入，警惕庄家借机高位砸盘出货", md)


if __name__ == "__main__":
    unittest.main()
