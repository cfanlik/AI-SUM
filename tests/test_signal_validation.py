from __future__ import annotations
import unittest
import sqlite3
import json
import os
import sys
import hashlib
import urllib.request
from pathlib import Path
from signal_validation.pipeline import execute_validation_pipeline, check_dual_api_real

sys.path.insert(0, '/opt/AI-SUM/meta-verdict')
from history_report import l3_gate_status

class MockResponse:
    def __init__(self, data: bytes, status: int = 200):
        self.data = data
        self.status = status
        self.code = status
    def read(self, *args, **kwargs):
        return self.data
    def getcode(self):
        return self.status

class TestSignalValidation(unittest.TestCase):
    def setUp(self):
        self.sum_db = '/opt/AI-SUM/select-sum.db'
        self.select_db = '/opt/select-coin/data/select.db'
        self.out_db = '/opt/AI-SUM/data/signal-validation.db'
        self.report_dir = '/opt/AI-SUM/report/unified'
        
    def test_pipeline_execution(self):
        # 拦截测试管道内部可能发生的真实 API 网络调用，默认成功
        original_urlopen = urllib.request.urlopen
        def mock_urlopen_success(req, *args, **kwargs):
            url = req.full_url
            if 'geckoterminal' in url:
                res_data = json.dumps({'data': {'attributes': {'base_token_price_usd': '1.0'}}})
            else:
                res_data = json.dumps({'pairs': [{'priceUsd': '1.0'}]})
            return MockResponse(res_data.encode(), 200)
            
        urllib.request.urlopen = mock_urlopen_success
        
        res = execute_validation_pipeline(
            self.sum_db, self.select_db, self.out_db, self.report_dir
        )
        
        self.assertGreater(res['total_events'], 0)
        self.assertGreater(res['identity_pass'], 0)
        self.assertGreater(res['coverage_pass'], 0)
        
        time_m = res['backtest']['time_metrics']
        self.assertFalse(time_m['has_enough'])
        self.assertIsNone(time_m['expectancy'])
        
        conn = sqlite3.connect(self.out_db)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        hist = c.execute('SELECT * FROM backtest_run_history').fetchall()
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]['status'], 'INSUFFICIENT_TRAINING_SAMPLE')
        
        manifest = c.execute('SELECT * FROM run_manifest').fetchall()
        self.assertEqual(len(manifest), 1)
        self.assertEqual(manifest[0]['schema_version'], 'v6.0')
        
        decisions = c.execute('SELECT * FROM signal_decision_result').fetchall()
        self.assertGreater(len(decisions), 0)
        for d_rec in decisions:
            self.assertIn(d_rec['outcome_incomplete'], {'PASS', 'outcome_incomplete'})
            
        conn.close()
        
        l3_report_text = l3_gate_status(self.out_db)
        self.assertIn("DENIED", l3_report_text)
        self.assertIn("insufficient_oos_sample", l3_report_text)
        self.assertIn("物理池绑定剔除", l3_report_text)
        self.assertIn("特征期覆盖度剔除", l3_report_text)
        self.assertIn("主池漂移拦截", l3_report_text)
        
        urllib.request.urlopen = original_urlopen
        print("✓ 基本流程与报告前置渲染测试通过！")

    def test_dual_api_validation_mock(self):
        print("\n=== 正在运行双源 API Mock 反例校验 (C8) ===")
        original_urlopen = urllib.request.urlopen
        
        # Mock 用例 1: 成功且无偏差 (dev = 0 <= 5%)
        def mock_urlopen_success(req, *args, **kwargs):
            url = req.full_url
            if 'geckoterminal' in url:
                res_data = json.dumps({'data': {'attributes': {'base_token_price_usd': '1.0'}}})
            else:
                res_data = json.dumps({'pairs': [{'priceUsd': '1.0'}]})
            return MockResponse(res_data.encode(), 200)
            
        urllib.request.urlopen = mock_urlopen_success
        self.assertTrue(check_dual_api_real('solana', '0xmock_pool', self.out_db))
        print("✓ Mock用例 1: 价格完全匹配测试通过。")
        
        # Mock 用例 2: 偏差超标 (dev = 10% > 5%)
        def mock_urlopen_deviation(req, *args, **kwargs):
            url = req.full_url
            if 'geckoterminal' in url:
                res_data = json.dumps({'data': {'attributes': {'base_token_price_usd': '1.0'}}})
            else:
                res_data = json.dumps({'pairs': [{'priceUsd': '1.1'}]})
            return MockResponse(res_data.encode(), 200)
            
        urllib.request.urlopen = mock_urlopen_deviation
        self.assertFalse(check_dual_api_real('solana', '0xmock_pool', self.out_db))
        print("✓ Mock用例 2: 价格偏差拦截测试通过。")
        
        # Mock 用例 3: 网络请求超时/故障
        def mock_urlopen_timeout(req, *args, **kwargs):
            raise urllib.error.URLError("timeout")
            
        urllib.request.urlopen = mock_urlopen_timeout
        self.assertFalse(check_dual_api_real('solana', '0xmock_pool', self.out_db))
        print("✓ Mock用例 3: 超时网络异常拦截测试通过。")
        
        # Mock 用例 4: 链/池不存在 (404 Not Found)
        def mock_urlopen_404(req, *args, **kwargs):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
            
        urllib.request.urlopen = mock_urlopen_404
        self.assertFalse(check_dual_api_real('solana', '0xmock_pool', self.out_db))
        print("✓ Mock用例 4: HTTP 404 资产不存在拦截测试通过。")
        
        urllib.request.urlopen = original_urlopen

if __name__ == '__main__':
    sys.exit(unittest.main())
