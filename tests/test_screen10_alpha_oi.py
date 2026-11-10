import unittest
import sys
import os
from pathlib import Path

# 测试在不同 sys.path 条件下的导入稳健性
REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from alpha_oi_scan import run_alpha_scan, evaluate_alpha_token

class TestScreen10AlphaOI(unittest.TestCase):
    def test_evaluate_alpha_token_logic(self):
        sample = {
            "symbol": "TEST_ALPHA",
            "token_address": "0x1234567890abcdef1234567890abcdef12345678",
            "chain": "BSC",
            "total_oi_usd": 6_000_000.0,
            "market_cap_usd": 30_000_000.0,
            "oi_change_24h": 0.05,
            "top20_ratio": 0.92,
            "gain_90d_ratio": 2.1,
            "price_now": 1.5,
            "primary_exchange": "BINANCE"
        }
        res = evaluate_alpha_token(sample)
        self.assertEqual(res["symbol"], "TEST_ALPHA")
        self.assertEqual(res["stage"], "🚀 L1-Alpha-OI")
        self.assertAlmostEqual(res["oi_mcap_ratio"], 0.2, places=3)
        self.assertEqual(res["verdict"], "黄金潜伏建仓期")

    def test_alpha_scan_execution(self):
        results = run_alpha_scan()
        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0)
        first = results[0]
        self.assertIn("symbol", first)
        self.assertIn("stage", first)
        self.assertIn("total_oi_usd", first)
        self.assertIn("oi_mcap_ratio", first)

if __name__ == '__main__':
    unittest.main()
