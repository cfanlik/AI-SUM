import unittest
import sys
import os

sys.path.insert(0, '/opt/AI-SUM/meta-verdict')
sys.path.insert(0, '/opt/AI-SUM/master-scan')

from cex_divergence_analyzer import analyze_cex_flow_divergence
from db_loader import get_connection

class TestScreen9AndDbLock(unittest.TestCase):
    def test_db_loader_connection_timeout(self):
        conn = get_connection()
        self.assertIsNotNone(conn)
        row = conn.execute("PRAGMA busy_timeout;").fetchone()
        self.assertGreaterEqual(row[0], 60000)
        conn.close()

    def test_screen9_divergence_engine(self):
        res = analyze_cex_flow_divergence()
        self.assertIn("top_alerts", res)
        self.assertIn("micro_matrix", res)
        self.assertIn("stats", res)
        # 若未触发新鲜度熔断，则验证筹码微观矩阵完备度
        if res.get("stats", {}).get("status") != "SOURCE_STALE":
            self.assertGreaterEqual(len(res["micro_matrix"]), 3)

if __name__ == '__main__':
    unittest.main()
