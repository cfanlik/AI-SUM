from __future__ import annotations
import unittest
import sqlite3
import os
import sys
import hashlib
from pathlib import Path
from signal_validation.pipeline import execute_validation_pipeline

sys.path.insert(0, '/opt/AI-SUM/meta-verdict')
from history_report import l3_gate_status

class TestSignalValidation(unittest.TestCase):
    def setUp(self):
        self.sum_db = '/opt/AI-SUM/select-sum.db'
        self.select_db = '/opt/select-coin/data/select.db'
        self.out_db = '/opt/AI-SUM/data/signal-validation.db'
        self.report_dir = '/opt/AI-SUM/report/unified'
        
    def test_pipeline_execution(self):
        # 1. 运行流水线跑通 P0-P4
        res = execute_validation_pipeline(
            self.sum_db, self.select_db, self.out_db, self.report_dir
        )
        
        # 2. 基本统计断言
        self.assertGreater(res['total_events'], 0)
        self.assertGreater(res['identity_pass'], 0)
        self.assertGreater(res['coverage_pass'], 0)
        
        # 3. 验证汇总层由于样本数不足 30 导致期望收益输出为 None (F2, F10)
        time_m = res['backtest']['time_metrics']
        self.assertFalse(time_m['has_enough'])
        self.assertIsNone(time_m['expectancy'])
        
        # 4. 验证数据库中 P3-P4 的落库结果 (backtest_run_history, signal_decision_result, run_manifest)
        conn = sqlite3.connect(self.out_db)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        hist = c.execute('SELECT * FROM backtest_run_history').fetchall()
        self.assertEqual(len(hist), 1, "应该只生成 1 条回测运行历史记录")
        self.assertEqual(hist[0]['status'], 'INSUFFICIENT_TRAINING_SAMPLE', "因样本不足应返回 INSUFFICIENT_TRAINING_SAMPLE")
        
        # 验证 C7 run_manifest 表
        manifest = c.execute('SELECT * FROM run_manifest').fetchall()
        self.assertEqual(len(manifest), 1, "应该只生成 1 条运行清单")
        self.assertEqual(manifest[0]['schema_version'], 'v6.0')
        self.assertGreater(len(manifest[0]['output_table_hash']), 0)
        
        decisions = c.execute('SELECT * FROM signal_decision_result').fetchall()
        self.assertGreater(len(decisions), 0, "决策记录应该大于0")
        
        # 验证结果期覆盖检测的 outcome_incomplete 字段落库
        for d_rec in decisions:
            self.assertIn(d_rec['outcome_incomplete'], {'PASS', 'outcome_incomplete'})
            
        conn.close()
        
        # 5. 验证 split_audit.csv 是否成功写入了全量逐行明细 (F12)
        csv_path = Path('/tmp/0802/test_run/split_audit.csv')
        self.assertTrue(csv_path.exists(), "split_audit.csv 文件应该存在")
        with csv_path.open('r', encoding='utf-8') as f:
            lines = f.readlines()
            self.assertGreater(len(lines), 1, "csv 应该包含表头和明细数据")
            
        # 6. 验证是否生成了正式报告并且包含了正确的拒绝原因
        report_path = Path(res['report_path'])
        self.assertTrue(report_path.exists(), "报告文件应该存在")
        report_content = report_path.read_text(encoding='utf-8')
        self.assertIn('insufficient_oos_sample', report_content, "拒绝原因应该包含 insufficient_oos_sample")
        self.assertIn('api_call_absent_or_failed', report_content, "API 校验未通过应该正确报告")
        
        # 7. 交叉验证 history_report 报告生成器的集成
        l3_report_text = l3_gate_status(self.out_db)
        self.assertIn("DENIED", l3_report_text, "报告中的 L3 决策应该显示 DENIED")
        self.assertIn("insufficient_oos_sample", l3_report_text, "报告中应包含拒绝原因编码")
        self.assertIn("Informal Candidate", l3_report_text, "报告中应指示降级身份为非正式候选")
        
        print("单元测试 PASS: PR 2 分支集成与回测明细导出测试 100% 通过！")

if __name__ == '__main__':
    unittest.main()
