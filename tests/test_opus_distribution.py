import sys, os, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'opus-scan'))

import config
from time_series_builder import TimeSeriesResult
from holder_profiler import build_profile, HolderProfile
from verdict_engine import evaluate, VerdictResult
from web_researcher import MarketContext


class TestOpusScanFixes(unittest.TestCase):

    def test_pure_sellers_detected(self):
        """测试纯卖出大户(buy_cnt=0, sell_cnt>0)被正确识别为出货者"""
        latest_holders = [
            {
                "wallet_address": "0x1111111111111111",
                "hold_percentage": 15.0,
                "buy_cnt": 0,
                "sell_cnt": 5,
                "is_accumulating": 0,
                "dex_ratio": 0.0,
                "net_inflow": -10000,
                "recent_48h_in": 0,
                "recent_48h_out": 5000,
            },
            {
                "wallet_address": "0x2222222222222222",
                "hold_percentage": 10.0,
                "buy_cnt": 2,
                "sell_cnt": 8,
                "is_accumulating": 0,
                "dex_ratio": 0.0,
                "net_inflow": -5000,
                "recent_48h_in": 0,
                "recent_48h_out": 2000,
            }
        ]
        earliest_holders = latest_holders
        hp = build_profile(latest_holders, earliest_holders, "2026-08-21 12:00:00", "2026-08-20 12:00:00")
        self.assertEqual(hp.seller_count, 2)
        self.assertEqual(hp.seller_hold_pct, 25.0)
        self.assertEqual(hp.distribution_48h_count, 2)

    def test_dex_native_token_confidence(self):
        """测试无 CEX 的 DEX 原生代币在强出货特征下能够超过 50% 门槛"""
        ts = TimeSeriesResult(
            chain="bsc",
            token_address="0xtest",
            symbol="TEST",
            snap_count=10,
            days_span=5.0,
            acc_cnt_earliest=5,
            acc_cnt_latest=1,
            acc_cnt_slope=-0.5,
            acc_hold_earliest=10.0,
            acc_hold_latest=2.0,
            acc_hold_growth_pct=-80.0,
            cex_hold_earliest=0.0,
            cex_hold_latest=0.0,
            cex_delta_pct=0.0,
            cex_hold_slope=0.0,
            supernode_delta=0,
            hidden_whale_latest=0,
            avg_score_latest=10.0,
            phase="distributing"
        )
        hp = HolderProfile(
            latest_snapshot="2026-08-21 12:00:00",
            earliest_snapshot="2026-08-20 12:00:00",
            top_n=30,
            sellers=[{"addr": "0x1...", "hold": 15.0, "buy": 0, "sell": 5}],
            seller_count=3,
            seller_hold_pct=30.0,
            fake_whale_count=3,
            fake_whale_hold_pct=25.0,
            distribution_48h_count=4,
            acc_count=1,
            acc_hold_pct=2.0,
            dex_verified_pct=0.0,
            strong_buyer_count=0
        )
        mc = MarketContext(
            has_data=True,
            lp_usd=100000,
            volume_24h=50000,
            vl_ratio=0.5,
            mcap_liq_ratio=10.0,
            gecko_pool_ok=True,
            volume_declining=True,
            lp_thin=False
        )

        vr = evaluate(ts, hp, mc)
        self.assertEqual(vr.verdict, "SLOW_DISTRIBUTION")
        self.assertGreaterEqual(vr.dist_confidence, 50.0)

    def test_accumulating_token_preserved(self):
        """测试正常吸筹代币的置信度与判定不被破坏"""
        ts = TimeSeriesResult(
            chain="bsc",
            token_address="0xacc",
            symbol="PRL",
            snap_count=10,
            days_span=5.0,
            acc_cnt_earliest=20,
            acc_cnt_latest=109,
            acc_cnt_slope=2.5,
            acc_hold_earliest=2.0,
            acc_hold_latest=9.0,
            acc_hold_growth_pct=350.0,
            cex_hold_earliest=25.0,
            cex_hold_latest=10.0,
            cex_delta_pct=-60.0,
            cex_hold_slope=-0.5,
            supernode_delta=0,
            hidden_whale_latest=0,
            avg_score_latest=85.0,
            phase="accelerating"
        )
        hp = HolderProfile(
            latest_snapshot="2026-08-21 12:00:00",
            earliest_snapshot="2026-08-20 12:00:00",
            top_n=30,
            sellers=[],
            seller_count=0,
            seller_hold_pct=0.0,
            fake_whale_count=0,
            fake_whale_hold_pct=0.0,
            distribution_48h_count=0,
            acc_count=109,
            acc_hold_pct=9.0,
            dex_verified_pct=100.0,
            strong_buyer_count=15,
            net_inflow_all_positive=True
        )
        mc = MarketContext(
            has_data=True,
            lp_usd=1500000,
            volume_24h=5000000,
            vl_ratio=3.5,
            mcap_liq_ratio=15.0,
            gecko_pool_ok=True,
            buy_sell_person_ratio=2.5,
            price_change_24h=5.0
        )
        vr = evaluate(ts, hp, mc)
        self.assertEqual(vr.verdict, "ACCUMULATING")
        self.assertGreaterEqual(vr.acc_confidence, 70.0)


if __name__ == '__main__':
    unittest.main()
