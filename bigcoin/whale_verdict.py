"""
whale-scan — 庄控裁决引擎
S1-S7 七维度信号融合 → 庄控置信度
"""
from __future__ import annotations
from dataclasses import dataclass, field
import config
from concentration_profiler import ConcentrationProfile
from drift_detector import DriftResult


@dataclass
class WhaleVerdict:
    """庄控裁决结果"""
    chain: str = ""
    token_address: str = ""
    symbol: str = ""

    confidence: float = 0.0          # 0-100%
    level: str = "CLEAN"             # HIGH / MEDIUM / LOW / CLEAN
    max_score: int = 0
    actual_score: int = 0

    signals: list[dict] = field(default_factory=list)   # [{dim, name, weight, hit, detail}]
    concentration: ConcentrationProfile | None = None
    drift: DriftResult | None = None

    # 快照/价格摘要
    snap_count: int = 0
    price_change_24h: float = 0.0
    mcap_liq_ratio: float = 0.0


def evaluate(
    chain: str,
    addr: str,
    sym: str,
    cp: ConcentrationProfile,
    dr: DriftResult,
    scores: dict | None,
    gecko: dict | None,
    latest_holders: list[dict],
) -> WhaleVerdict:
    """七维度庄控评估"""
    v = WhaleVerdict(
        chain=chain, token_address=addr, symbol=sym,
        concentration=cp, drift=dr, snap_count=dr.snap_count,
    )

    sigs = []

    # ════════════════════════════════════════
    # S1: 极端集中度 (weight=18)
    # ════════════════════════════════════════
    sigs.append(_sig("S1", "top2_over_50pct", 5,
                      cp.top2_hold > config.TOP2_HOLD_THRESHOLD,
                      f"Top2={cp.top2_hold:.1f}%"))
    sigs.append(_sig("S1", "top5_over_80pct", 3,
                      cp.top5_hold > config.TOP5_HOLD_THRESHOLD,
                      f"Top5={cp.top5_hold:.1f}%"))
    sigs.append(_sig("S1", "top10_over_90pct", 2,
                      cp.top10_hold > config.TOP10_HOLD_THRESHOLD,
                      f"Top10={cp.top10_hold:.1f}%"))
    sigs.append(_sig("S1", "top20_over_95pct", 1,
                      cp.top20_hold > config.TOP20_HOLD_THRESHOLD,
                      f"Top20={cp.top20_hold:.1f}%"))
    sigs.append(_sig("S1", "top2_zero_dex", 4,
                      0 <= cp.top2_avg_dex < config.TOP2_DEX_MAX,
                      f"Top2 DEX={cp.top2_avg_dex:.2f}"))
    sigs.append(_sig("S1", "top2_is_wallet", 3,
                      cp.top2_all_wallet,
                      "Top2均为普通钱包"))

    # ════════════════════════════════════════
    # S2: 持仓漂移 (weight=14)
    # ════════════════════════════════════════
    sigs.append(_sig("S2", "whale1_passive_drift", 5,
                      dr.whale1_hold_delta > config.WHALE1_DRIFT_MIN and dr.whale1_buy_unchanged,
                      f"#1: {dr.whale1_hold_first:.1f}%→{dr.whale1_hold_last:.1f}% (+{dr.whale1_hold_delta:.1f}%), 买入不变"))
    sigs.append(_sig("S2", "whale2_passive_drift", 4,
                      dr.whale2_hold_delta > config.WHALE2_DRIFT_MIN and dr.whale2_buy_unchanged,
                      f"#2: {dr.whale2_hold_first:.1f}%→{dr.whale2_hold_last:.1f}% (+{dr.whale2_hold_delta:.1f}%), 买入不变"))
    sigs.append(_sig("S2", "multi_drift_3plus", 3,
                      dr.passive_drift_count >= config.MULTI_DRIFT_MIN,
                      f"Top10中{dr.passive_drift_count}个被动漂移"))

    # acc_pump_divergence: acc下降 且 价格上涨
    price_chg = (gecko or {}).get("price_change_24h", 0) or 0
    acc_declining = dr.acc_cnt_last < dr.acc_cnt_first * 0.7 if dr.acc_cnt_first > 0 else False
    sigs.append(_sig("S2", "acc_pump_divergence", 2,
                      acc_declining and price_chg > config.ACC_PUMP_PRICE_MIN,
                      f"acc {dr.acc_cnt_first}→{dr.acc_cnt_last}, 价格+{price_chg:.0f}%"))

    # ════════════════════════════════════════
    # S3: 吸筹反向 (weight=8)
    # ════════════════════════════════════════
    acc_pct_real = (scores or {}).get("acc_pct_real", 100) or 100
    composite = (scores or {}).get("composite", 100) or 100
    dex_pct = (scores or {}).get("direct_dex_acc_pct", 100) or 100

    sigs.append(_sig("S3", "low_acc_pct", 2,
                      acc_pct_real < config.LOW_ACC_PCT,
                      f"acc_pct={acc_pct_real:.1f}%"))
    sigs.append(_sig("S3", "low_composite", 1,
                      composite < config.LOW_COMPOSITE,
                      f"composite={composite:.1f}"))
    sigs.append(_sig("S3", "low_dex_purity", 2,
                      dex_pct < config.LOW_DEX_PCT,
                      f"dex_acc={dex_pct:.1f}%"))

    # acc_freefall: acc_pct 首末下降 >80%
    freefall = False
    if dr.acc_pct_first > 0:
        drop_pct = (1 - dr.acc_pct_last / dr.acc_pct_first) * 100
        freefall = drop_pct > config.ACC_FREEFALL_PCT
    sigs.append(_sig("S3", "acc_freefall", 3,
                      freefall,
                      f"acc_pct {dr.acc_pct_first:.1f}%→{dr.acc_pct_last:.1f}%"))

    # ════════════════════════════════════════
    # S4: 价格异动 (weight=10)
    # ════════════════════════════════════════
    v.price_change_24h = price_chg
    reserve = (gecko or {}).get("reserve_usd", 1) or 1
    mcap = (gecko or {}).get("market_cap_usd", 0) or 0
    mcap_liq = mcap / reserve if reserve > 0 else 0
    v.mcap_liq_ratio = mcap_liq

    sigs.append(_sig("S4", "pump_100pct", 4,
                      price_chg > config.PUMP_PCT_THRESHOLD,
                      f"24h +{price_chg:.0f}%"))
    sigs.append(_sig("S4", "sustained_pump", 2,
                      price_chg > config.SUSTAINED_PUMP_PCT * 2,
                      f"持续拉升 +{price_chg:.0f}%"))
    sigs.append(_sig("S4", "extreme_mcap_liq", 4,
                      mcap_liq > config.MCAP_LIQ_THRESHOLD,
                      f"M/L={mcap_liq:.1f}x"))

    # ════════════════════════════════════════
    # S5: 派发预兆 (weight=9)
    # ════════════════════════════════════════
    w1_signals = ""
    w1_48h_out = 0
    w1_48h_in = 0
    if latest_holders:
        w1_signals = latest_holders[0].get("acc_signals", "") or ""
        w1_48h_out = latest_holders[0].get("recent_48h_out", 0) or 0
        w1_48h_in = latest_holders[0].get("recent_48h_in", 0) or 0

    sigs.append(_sig("S5", "whale1_distributing", 5,
                      config.DIST_KEYWORD in w1_signals,
                      f"#1 信号含'{config.DIST_KEYWORD}'"))
    sigs.append(_sig("S5", "whale1_48h_outflow", 4,
                      w1_48h_out > 0 and w1_48h_in == 0,
                      f"#1 48h出={w1_48h_out:,.0f}, 入={w1_48h_in:,.0f}"))

    # ════════════════════════════════════════
    # S6: 合约锁仓低流通 (weight=4)
    # ════════════════════════════════════════
    sigs.append(_sig("S6", "high_contract_hold", 2,
                      cp.contract_pct > config.CONTRACT_HOLD_THRESHOLD,
                      f"合约持仓={cp.contract_pct:.1f}%"))
    sigs.append(_sig("S6", "low_circulation", 2,
                      cp.top300_hold > config.TOP300_COVERAGE_THRESHOLD,
                      f"Top300={cp.top300_hold:.1f}%"))

    # ════════════════════════════════════════
    # S7: 基本面缺失 (weight=2)
    # ════════════════════════════════════════
    sigs.append(_sig("S7", "zero_mcap", 1,
                      mcap == 0,
                      "token_names 无市值"))
    sigs.append(_sig("S7", "no_gecko_data", 1,
                      gecko is None,
                      "无 Gecko 市场数据"))

    # ── 计算置信度 ──
    v.signals = sigs
    v.max_score = sum(s["weight"] for s in sigs)
    v.actual_score = sum(s["weight"] for s in sigs if s["hit"])
    v.confidence = round(v.actual_score / v.max_score * 100, 1) if v.max_score > 0 else 0

    if v.confidence >= config.LEVEL_HIGH:
        v.level = "HIGH"
    elif v.confidence >= config.LEVEL_MEDIUM:
        v.level = "MEDIUM"
    elif v.confidence >= config.LEVEL_LOW:
        v.level = "LOW"
    else:
        v.level = "CLEAN"

    return v


def _sig(dim: str, name: str, weight: int, hit: bool, detail: str) -> dict:
    return {"dim": dim, "name": name, "weight": weight, "hit": hit, "detail": detail}
