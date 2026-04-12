"""
opus-scan — 双维度评分引擎
每个代币产出 acc_confidence + dist_confidence (0-100%)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import config
from time_series_builder import TimeSeriesResult
from holder_profiler import HolderProfile
from web_researcher import MarketContext


@dataclass
class Evidence:
    name: str
    weight: int
    matched: bool
    detail: str


@dataclass
class VerdictResult:
    chain: str
    token_address: str
    symbol: str
    snap_count: int

    acc_confidence: float = 0.0
    dist_confidence: float = 0.0

    acc_evidence: list[Evidence] = field(default_factory=list)
    dist_evidence: list[Evidence] = field(default_factory=list)

    verdict: str = "NEUTRAL"
    verdict_detail: str = ""

    # 关键指标快照
    acc_cnt: int = 0
    acc_hold_pct: float = 0.0
    dex_verified_pct: float = 0.0
    cex_delta_pct: float = 0.0
    seller_count: int = 0
    seller_hold_pct: float = 0.0
    fake_whale_count: int = 0
    dist_48h_count: int = 0
    phase: str = ""
    volume_24h: Optional[float] = None
    lp_usd: Optional[float] = None


def evaluate(
    ts: TimeSeriesResult,
    hp: HolderProfile,
    mc: Optional[MarketContext] = None,
) -> VerdictResult:
    if mc is None:
        mc = MarketContext()

    vr = VerdictResult(
        chain=ts.chain,
        token_address=ts.token_address,
        symbol=ts.symbol,
        snap_count=ts.snap_count,
        acc_cnt=ts.acc_cnt_latest,
        acc_hold_pct=hp.acc_hold_pct,
        dex_verified_pct=hp.dex_verified_pct,
        cex_delta_pct=ts.cex_delta_pct,
        seller_count=hp.seller_count,
        seller_hold_pct=hp.seller_hold_pct,
        fake_whale_count=hp.fake_whale_count,
        dist_48h_count=hp.distribution_48h_count,
        phase=ts.phase,
        volume_24h=mc.pool_volume_24h or mc.volume_24h,
        lp_usd=mc.lp_usd,
    )

    # ── 吸筹置信度 ──
    acc_checks = [
        ("acc_trend_up", 3,
         ts.acc_cnt_slope > 0,
         f"acc_cnt 斜率={ts.acc_cnt_slope:+.2f}"),

        ("acc_hold_growing", 3,
         ts.acc_hold_growth_pct > config.ACC_HOLD_GROWTH_MIN,
         f"acc_hold 增长{ts.acc_hold_growth_pct:+.1f}%"),

        ("dex_rate_high", 3,
         hp.dex_verified_pct > config.ACC_DEX_RATE_MIN,
         f"DEX真金率 {hp.dex_verified_pct:.1f}%"),

        ("strong_buyers", 2,
         hp.strong_buyer_count >= config.ACC_STRONG_BUYER_MIN,
         f"强买入者 {hp.strong_buyer_count} 个"),

        ("no_major_seller", 2,
         hp.seller_hold_pct < config.DIST_SELLER_HOLD_MIN,
         f"出货者持仓 {hp.seller_hold_pct:.1f}%"),

        ("net_inflow_positive", 2,
         hp.net_inflow_all_positive,
         f"吸筹者净流入{'全正' if hp.net_inflow_all_positive else '有负'}"),

        ("cex_outflow", 3,
         ts.cex_delta_pct < -3 and ts.cex_hold_slope < -0.2,
         f"CEX {ts.cex_hold_earliest:.1f}%\u2192{ts.cex_hold_latest:.1f}% (\u659c\u7387{ts.cex_hold_slope:+.2f})"),

        ("price_not_pumped", 1,
         mc.price_change_24h is None or mc.price_change_24h < config.ACC_PRICE_PUMP_MAX,
         f"价格24h {mc.price_change_24h or 0:+.1f}%"),

        # ── Gecko Pool 增强 ──
        ("buy_sell_person_ratio", 2,
         mc.buy_sell_person_ratio is not None and mc.buy_sell_person_ratio >= config.POOL_BUY_SELL_PERSON_MIN,
         f"买/卖人数比 {mc.buy_sell_person_ratio or 0:.1f}"),
    ]

    _calc_confidence(vr, acc_checks, is_acc=True)

    # ── 出货置信度 ──
    dist_checks = [
        ("cex_declining", 3,
         ts.cex_delta_pct < -config.DIST_CEX_DECLINE_MIN,
         f"CEX持仓 {ts.cex_delta_pct:+.1f}%"),

        ("major_seller", 3,
         hp.seller_count > 0 and any(
             s.get("hold", 0) >= 1.0 for s in hp.sellers),
         f"出货者 {hp.seller_count} 个, 持仓 {hp.seller_hold_pct:.1f}%"),

        ("fake_whales", 3,
         hp.fake_whale_count >= config.DIST_FAKE_WHALE_MIN,
         f"假鲸鱼 {hp.fake_whale_count} 个, 持仓 {hp.fake_whale_hold_pct:.1f}%"),

        ("distribution_48h", 2,
         hp.distribution_48h_count >= config.DIST_48H_SELLER_MIN,
         f"48h派发者 {hp.distribution_48h_count} 个"),

        ("acc_hold_stagnant", 2,
         ts.acc_hold_growth_pct < 5 and ts.acc_cnt_slope < 0.1,
         f"acc增长 {ts.acc_hold_growth_pct:+.1f}%, 斜率 {ts.acc_cnt_slope:+.2f}"),

        ("seller_hold_heavy", 2,
         hp.seller_hold_pct >= config.DIST_SELLER_HOLD_MIN,
         f"出货者持仓 {hp.seller_hold_pct:.1f}% ≥ {config.DIST_SELLER_HOLD_MIN}%"),

        ("price_rising_cover", 1,
         mc.price_change_24h is not None and mc.price_change_24h > 10,
         f"价格上涨 {mc.price_change_24h or 0:+.1f}% 掩护出货"),

        ("acc_insufficient", 1,
         hp.acc_hold_pct < config.DIST_ACC_HOLD_LOW,
         f"吸筹者持仓 {hp.acc_hold_pct:.1f}% < {config.DIST_ACC_HOLD_LOW}%"),

        # ── Gecko Pool 增强 ──
        ("volume_declining", 2,
         mc.volume_declining if mc.gecko_pool_ok else False,
         f"24h量缩 Vol={mc.pool_volume_24h or 0:.0f}"),

        ("lp_thin", 1,
         mc.lp_thin if mc.gecko_pool_ok else False,
         f"LP不足 ${mc.pool_lp_usd or 0:,.0f}"),

        ("price_7d_drop", 1,
         mc.price_change_7d is not None and mc.price_change_7d < config.POOL_PRICE_7D_DROP_PCT,
         f"7d\u4ef7\u683c {mc.price_change_7d or 0:+.1f}%"),

        ("cex_inflow", 2,
         ts.cex_delta_pct > 3 and ts.cex_hold_slope > 0.2,
         f"CEX {ts.cex_hold_earliest:.1f}%\u2192{ts.cex_hold_latest:.1f}% (\u659c\u7387{ts.cex_hold_slope:+.2f})"),
    ]

    _calc_confidence(vr, dist_checks, is_acc=False)

    # ── 裁决 ──
    _determine_verdict(vr)

    return vr


def _calc_confidence(vr: VerdictResult, checks: list, is_acc: bool):
    total_weight = 0
    hit_weight = 0
    for name, weight, matched, detail in checks:
        ev = Evidence(name=name, weight=weight, matched=matched, detail=detail)
        if is_acc:
            vr.acc_evidence.append(ev)
        else:
            vr.dist_evidence.append(ev)
        total_weight += weight
        if matched:
            hit_weight += weight

    conf = round(hit_weight / max(total_weight, 1) * 100, 1)
    if is_acc:
        vr.acc_confidence = conf
    else:
        vr.dist_confidence = conf


def _determine_verdict(vr: VerdictResult):
    if vr.dist_confidence >= 50 and vr.acc_confidence >= 50:
        vr.verdict = "MIXED"
        vr.verdict_detail = f"吸筹({vr.acc_confidence:.0f}%)与出货({vr.dist_confidence:.0f}%)并存"
    elif vr.dist_confidence >= 50:
        vr.verdict = "SLOW_DISTRIBUTION"
        vr.verdict_detail = f"出货置信度 {vr.dist_confidence:.0f}%"
    elif vr.acc_confidence >= 50:
        vr.verdict = "ACCUMULATING"
        vr.verdict_detail = f"吸筹置信度 {vr.acc_confidence:.0f}%"
    else:
        vr.verdict = "NEUTRAL"
        vr.verdict_detail = f"吸筹{vr.acc_confidence:.0f}%/出货{vr.dist_confidence:.0f}% 均不显著"
