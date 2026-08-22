import unittest
import os
import sys
import sqlite3

# 确保 AI-SUM 路径在顶层
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from anomaly_watch.impulse_surge_analyzer import ImpulseSurgeAnalyzer
from anomaly_watch.generate_live_observation_report import validate_json_document

class TestAnomalyFreshnessAndNoneHandling(unittest.TestCase):
    def test_impulse_surge_none_format(self):
        """测试历史不足的新币当 slope_15d 为 None 时不会抛出格式化异常"""
        analyzer = ImpulseSurgeAnalyzer()
        
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        cur.execute("""
            CREATE TABLE token_history (
                token_address TEXT,
                reserve_usd REAL DEFAULT 10000.0,
                pnl_ratio REAL,
                whale_entered INTEGER DEFAULT 0,
                whale_exited INTEGER DEFAULT 0,
                volume_24h REAL DEFAULT 0,
                computed_date TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE meta_snapshots (
                token_address TEXT,
                meta_verdict TEXT,
                scan_time TEXT
            )
        """)
        # 插入代币数据
        cur.execute("INSERT INTO token_history VALUES ('0xtest', 10000.0, 100.0, 0, 0, 1000, '2026-08-22')")
        cur.execute("INSERT INTO token_history VALUES ('0xtest', 10000.0, 10.0, 0, 0, 1000, '2026-08-21')")
        cur.execute("INSERT INTO token_history VALUES ('0xtest', 10000.0, 5.0, 0, 0, 1000, '2026-08-20')")
        
        # 执行 _calc_token_surge 应正常返回而不崩溃
        res = analyzer._calc_token_surge(cur, '0xtest', 'TEST', 'bsc')
        self.assertIsNotNone(res)
        self.assertEqual(res.token_symbol, 'TEST')
        conn.close()

    def test_json_document_validation(self):
        """测试包含了 data_freshness_status 的 report document 能够通过 schema 校验"""
        sample_doc = {
            "report_metadata": {
                "report_generated_at_utc": "2026-08-22T00:00:00Z",
                "as_of_utc": "2026-08-22T00:00:00Z",
                "evaluation_status": "NOT_EVALUATED",
                "evaluation_reason": "INSUFFICIENT_TRAINING_SAMPLE",
                "data_freshness_status": "FRESH",
                "git_commit": "abcdef",
                "config_hash": "123456"
            },
            "rows": []
        }
        self.assertTrue(validate_json_document(sample_doc))

if __name__ == '__main__':
    unittest.main()
