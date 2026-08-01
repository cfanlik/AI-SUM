from __future__ import annotations
import unittest
import sqlite3
import os
from pathlib import Path
from signal_validation.pipeline import execute_validation_pipeline

class TestSignalValidation(unittest.TestCase):
    def setUp(self):
        self.sum_db = '/opt/AI-SUM/select-sum.db'
        self.select_db = '/opt/select-coin/data/select.db'
        # 统一使用测试独立库，不改变生产数据
        self.out_db = '/opt/AI-SUM/data/signal-validation.db'
        
    def test_pipeline_execution(self):
        # 1. 运行流水线
        res = execute_validation_pipeline(self.sum_db, self.select_db, self.out_db)
        
        self.assertGreater(res['total_events'], 0, "A事件提取数量应大于0")
        self.assertGreater(res['identity_pass'], 0, "成功绑定主池数量应大于0")
        self.assertGreater(res['coverage_pass'], 0, "覆盖率审计通过数应大于0")
        self.assertGreater(res['saved_snapshots'], 0, "落库特征快照数应大于0")
        
        # 2. 验证数据物理隔离与正确性
        conn = sqlite3.connect(self.out_db)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        # 3. 验证 asset_identity
        identities = c.execute('SELECT * FROM asset_identity').fetchall()
        for idt in identities:
            # 链信度必须被保存 (F10)
            self.assertIn(idt['chain_confidence'], {'high', 'fail'})
            self.assertIn(idt['identity_pass'], {0, 1})
            # 候选池必须保存 (C1)
            self.assertTrue(idt['candidate_pools'].startswith('['))
            
        # 4. 验证 daily_feature_snapshot 与命名规范 (C2)
        snapshots = c.execute('SELECT * FROM daily_feature_snapshot LIMIT 100').fetchall()
        for snap in snapshots:
            # 确保成交量代理命名无误 (C2)
            self.assertIn('daily_24h_rolling_volume_proxy', snap.keys())
            self.assertGreater(snap['price_usd'], 0)
            self.assertGreater(snap['reserve_usd'], 0)
            self.assertIsNotNone(snap['source_scan_time'])
            
        conn.close()
        print("单元测试 PASS: 所有断言均成功通过！")

if __name__ == '__main__':
    unittest.main()
