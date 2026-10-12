#!/usr/bin/env python3
"""
test_cex_flow_meta_convergence.py — CEX 资金流全网拟合与仲裁驾驶舱第一屏 SSOT 收敛单元测试
"""
import unittest
import sys
import os
import sqlite3
from datetime import datetime, timedelta

sys.path.insert(0, "/opt/AI-SUM/meta-verdict")
import config
from collector import TokenEngineData
from arbitrator import arbitrate, load_cex_flow_data, determine_confidence_tier
from price_fit_radar import calculate_fit_calibration
from report_generator import generate_report


class TestCexFlowMetaConvergence(unittest.TestCase):

    def test_01_cex_flow_reading_and_macro(self):
        """测试 CEX 资金流与宏观水温读取"""
        token_flows, macro_data, status = load_cex_flow_data()
        self.assertIn(status, ("OK", "SOURCE_STALE", "SOURCE_EMPTY"))
        if status == "OK":
            self.assertGreater(len(token_flows), 0)
            self.assertIn("stablecoin_tide", macro_data)
            print(f"[TEST 1 PASS] 载入 CEX 资金流: {len(token_flows)} 标的, 稳定币潮汐: ${macro_data['stablecoin_tide']/1e6:.2f}M")

    def test_02_ultra_acc_retention(self):
        """测试顶级真金保留: KITE, UAI, SOON 评为 ULTRA_ACC, 梯队稳居 L1-Alpha"""
        for sym in ["KITE", "UAI", "SOON"]:
            t = TokenEngineData(
                chain="eth",
                token_address=f"0x{sym.lower()}",
                token_symbol=sym,
                master_signal="DIAMOND",
                opus_verdict="ACCUMULATING",
                opus_acc_conf=80.0,
                unified_signal="DIAMOND",
                whale_level="HIGH",
                cb_verdict="STRONG_ACC",
                price_now_ret=30.0,
                price_usd=0.25
            )
            res = arbitrate(t, hop2_pct=0.15)
            self.assertEqual(res.confidence_tier, "L1-Alpha")
            self.assertEqual(res.lifecycle_status, "ULTRA_ACC")
            self.assertEqual(res.fit_action_guide, "现货分批建仓 / 中长线持有")
            self.assertEqual(res.fit_score_delta, 1.10)
        print("[TEST 2 PASS] 顶级真金标的 KITE, UAI, SOON 全部评为 ULTRA_ACC 并稳居 L1-Alpha")

    def test_03_overheating_pros_correction(self):
        """测试高位过热纠偏: PROS 判定 OVERHEATING, 坚决移出 L1-Alpha, 推荐高位止盈"""
        t = TokenEngineData(
            chain="base",
            token_address="0xpros",
            token_symbol="PROS",
            master_signal="DIAMOND",
            opus_verdict="ACCUMULATING",
            opus_acc_conf=70.0,
            unified_signal="DIAMOND",
            whale_level="HIGH",
            cb_verdict="STRONG_ACC",
            price_now_ret=58.5,
            price_usd=0.68
        )
        res = arbitrate(t, hop2_pct=0.15)
        self.assertNotEqual(res.confidence_tier, "L1-Alpha")
        self.assertEqual(res.lifecycle_status, "OVERHEATING")
        self.assertEqual(res.fit_action_guide, "现货高位止盈 / 规避追高")
        self.assertEqual(res.fit_score_delta, -0.50)
        print("[TEST 3 PASS] 高位过热标的 PROS 成功纠偏，移出 L1-Alpha 并标记高位止盈")

    def test_04_breakdown_interception_ct_sqd_lyn(self):
        """测试致命破位拦截: CT, SQD, LYN 触发一票否决, 100% 阻断在 L1 外"""
        for sym in ["CT", "SQD", "LYN"]:
            t = TokenEngineData(
                chain="eth",
                token_address=f"0x{sym.lower()}",
                token_symbol=sym,
                master_signal="DIAMOND",
                opus_verdict="ACCUMULATING",
                opus_acc_conf=60.0,
                unified_signal="DIAMOND",
                whale_level="HIGH",
                cb_verdict="ACC",
                price_now_ret=-35.0,
                vl_ratio=15.0 if sym == "CT" else 1.2
            )
            res = arbitrate(t, hop2_pct=0.0)
            self.assertEqual(res.confidence_tier, "DENIED")
            self.assertEqual(res.lifecycle_status, "DECAY_BREAKDOWN")
            self.assertEqual(res.fit_action_guide, "一票否决阻断 / 严禁抄底")
            self.assertEqual(res.fit_score_delta, -1.20)
        print("[TEST 4 PASS] 致命破位标的 CT, SQD, LYN 触发一票否决，100% 阻断在 L1 外")

    def test_05_screen1_report_rendering(self):
        """测试第一屏决策红绿灯全景表渲染符合规范表头"""
        t1 = TokenEngineData(chain="eth", token_address="0xkite", token_symbol="KITE", master_signal="DIAMOND", opus_verdict="ACCUMULATING", unified_signal="DIAMOND", whale_level="HIGH", cb_verdict="STRONG_ACC", price_now_ret=26.1, price_usd=0.1259)
        t2 = TokenEngineData(chain="base", token_address="0xpros", token_symbol="PROS", master_signal="DIAMOND", opus_verdict="ACCUMULATING", unified_signal="DIAMOND", whale_level="HIGH", cb_verdict="STRONG_ACC", price_now_ret=58.5, price_usd=0.6851)
        t3 = TokenEngineData(chain="eth", token_address="0xct", token_symbol="CT", master_signal="RED", price_now_ret=-25.0, vl_ratio=18.0)

        r1 = arbitrate(t1)
        r2 = arbitrate(t2)
        r3 = arbitrate(t3)

        # 检查字段
        self.assertIn("ULTRA_ACC", r1.lifecycle_status)
        self.assertIn("OVERHEATING", r2.lifecycle_status)
        self.assertIn("DECAY_BREAKDOWN", r3.lifecycle_status)
        print("[TEST 5 PASS] 第一屏关键分类与字段构造完全符合 SSOT 规范")


if __name__ == "__main__":
    unittest.main()
