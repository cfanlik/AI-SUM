import sys
import unittest
import sqlite3
from dataclasses import dataclass

sys.path.insert(0, '/opt/AI-SUM')
sys.path.insert(0, '/opt/AI-SUM/meta-verdict')

import price_fit_radar as pfr
import arbitrator as arb
import report_generator as rep
from collector import TokenEngineData

class TestRetentionAndRadar(unittest.TestCase):
    def test_01_token_engine_data_has_retention_7d(self):
        """测试 TokenEngineData 正确包含 retention_7d 字段"""
        t = TokenEngineData(chain="bsc", token_address="0x123", retention_7d=88.5)
        self.assertEqual(t.retention_7d, 88.5)

    def test_02_kite_strong_accumulation_with_retention(self):
        """测试 KITE: consec=46, retention=96.6% -> +1.10 (0.80+0.30), 10.60分, ACCELERATING"""
        r = arb.MetaResult(chain="bsc", token_address="0xkite", token_symbol="KITE", meta_score=9.50)
        t = TokenEngineData(chain="bsc", token_address="0xkite", acc_count_latest=46, retention_7d=96.6)
        calib = pfr.calculate_fit_calibration(r, t)
        print("KITE calibration:", calib)
        self.assertEqual(calib.fit_delta, 1.10)
        self.assertEqual(calib.calibrated_score, 10.60)
        self.assertEqual(calib.retention_7d, 96.6)
        self.assertEqual(calib.lifecycle_status, "ACCELERATING")
        self.assertEqual(calib.action_guide, "核心强动能，真金持续蓄力")

    def test_03_take_death_spiral_and_churn_alert(self):
        """测试 TAKE: consec=37, retention=39.4% (<50%), cb_verdict='DEATH_SPIRAL' -> 拦截出货，扣分并预警"""
        r = arb.MetaResult(chain="bsc", token_address="0xtake", token_symbol="TAKE", meta_score=8.00, cb_verdict="DEATH_SPIRAL")
        t = TokenEngineData(chain="bsc", token_address="0xtake", acc_count_latest=37, retention_7d=39.4)
        calib = pfr.calculate_fit_calibration(r, t)
        print("TAKE calibration:", calib)
        # -0.50 (留存衰退) + -0.80 (DEATH_SPIRAL) = -1.30
        self.assertEqual(calib.fit_delta, -1.30)
        self.assertEqual(calib.calibrated_score, 6.70)
        self.assertEqual(calib.retention_7d, 39.4)
        self.assertEqual(calib.lifecycle_status, "DIST_WARN")
        self.assertIn("死亡螺旋", calib.action_guide)

    def test_04_screen8_filtering_take_blocked_from_l1(self):
        """测试第八屏筛选：TAKE 严禁进入 L1 冲刺池，必须进入 Alert 预警池；KITE 进入 L1 冲刺池"""
        # KITE
        r_kite = arb.MetaResult(chain="bsc", token_address="0xkite", token_symbol="KITE", meta_score=9.50, confidence_tier="L1-Alpha")
        t_kite = TokenEngineData(chain="bsc", token_address="0xkite", acc_count_latest=46, retention_7d=96.6)
        r_kite._calib_result = pfr.calculate_fit_calibration(r_kite, t_kite)

        # TAKE
        r_take = arb.MetaResult(chain="bsc", token_address="0xtake", token_symbol="TAKE", meta_score=8.00, confidence_tier="L1-Squeeze", cb_verdict="DEATH_SPIRAL")
        t_take = TokenEngineData(chain="bsc", token_address="0xtake", acc_count_latest=37, retention_7d=39.4)
        r_take._calib_result = pfr.calculate_fit_calibration(r_take, t_take)

        screen8 = pfr.build_screen8_radar_data([r_kite, r_take])
        
        # L1 冲刺池中必须有 KITE，绝对没有 TAKE
        l1_symbols = [x["token_symbol"] for x in screen8["l1_top"]]
        self.assertIn("KITE", l1_symbols)
        self.assertNotIn("TAKE", l1_symbols)
        self.assertEqual(screen8["l1_top"][0]["retention_7d"], 96.6)

        # Alert 预警池中必须包含 TAKE
        alert_symbols = [x["token_symbol"] for x in screen8["alert_top"]]
        self.assertIn("TAKE", alert_symbols)
        print("Screen8 pools verified:", "L1:", l1_symbols, "Alert:", alert_symbols)

    def test_05_markdown_report_renders_non_zero_retention_and_badge(self):
        """测试 Markdown 看板正确渲染非零留存与状态勋章"""
        r_kite = arb.MetaResult(chain="bsc", token_address="0xkite", token_symbol="KITE", meta_score=9.50, confidence_tier="L1-Alpha")
        r_kite._calib_result = pfr.FitCalibrationResult(
            token_address="0xkite", calibrated_score=10.60, fit_delta=1.10, consec_acc=46,
            score_sigma=0.0, retention_7d=96.6, lifecycle_status="ACCELERATING", action_guide="真金持续蓄力"
        )
        r_take = arb.MetaResult(chain="bsc", token_address="0xtake", token_symbol="TAKE", meta_score=8.00, confidence_tier="L1-Squeeze")
        r_take._calib_result = pfr.FitCalibrationResult(
            token_address="0xtake", calibrated_score=6.70, fit_delta=-1.30, consec_acc=37,
            score_sigma=0.0, retention_7d=39.4, lifecycle_status="DIST_WARN", action_guide="成本螺旋预警"
        )
        screen8 = pfr.build_screen8_radar_data([r_kite, r_take])
        report = rep.generate_report(
            acc_list=[r_kite, r_take], dist_list=[], all_count=2, scan_time="2026-09-26 21:30:00",
            all_arbitrated=[r_kite, r_take], screen8_data=screen8
        )
        self.assertIn("96.6%", report)
        self.assertIn("39.4%", report)
        self.assertIn("加速蓄力", report)
        self.assertIn("出货阻断", report)
        print("Markdown report rendering verified successfully!")

if __name__ == '__main__':
    suite = unittest.TestLoader().loadTestsFromTestCase(TestRetentionAndRadar)
    runner = unittest.TextTestRunner(verbosity=2)
    res = runner.run(suite)
    if not res.wasSuccessful():
        sys.exit(1)
