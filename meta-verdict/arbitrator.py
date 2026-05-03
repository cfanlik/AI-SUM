"""
meta-verdict 仲裁引擎
5 引擎投票 → 加权积分 → 统一排名 + 生命周期状态机
"""
from __future__ import annotations
from dataclasses import dataclass, field
import config
from collector import TokenEngineData


@dataclass
class MetaResult:
    chain: str
    token_address: str
    token_symbol: str

    meta_score: float = 0.0
    meta_verdict: str = "NEUTRAL"   # ACC / DIST / NEUTRAL
    engine_hits: int = 0

    # 各引擎贡献分
    master_score:  float = 0.0
    opus_score:    float = 0.0
    unified_score: float = 0.0
    whale_score:   float = 0.0
    cb_score:      float = 0.0

    # 原始数据透传
    master_signal:  str   = ""
    opus_verdict:   str   = ""
    unified_signal: str   = ""
    whale_level:    str   = ""
    cb_verdict:     str   = ""

    # 价格数据（来自 cost-basis-scan）
    cb_gecko_price: float = 0.0
    cb_vwap:        float = 0.0
    cb_windfall_pct:float = 0.0
    cb_acc_pct:     float = 0.0
    cb_dist_pct:    float = 0.0
    cb_signals:     str   = ""

    # 生命周期
    stage: str = ""   # ACCUMULATING / CONTROLLED / DISTRIBUTING / WATCHLIST / NEUTRAL


def arbitrate(data: TokenEngineData) -> MetaResult:
    """5 引擎加权积分仲裁"""
    r = MetaResult(
        chain=data.chain,
        token_address=data.token_address,
        token_symbol=data.token_symbol,
        engine_hits=data.engine_hits,
        master_signal=data.master_signal,
        opus_verdict=data.opus_verdict,
        unified_signal=data.unified_signal,
        whale_level=data.whale_level,
        cb_verdict=data.cb_verdict,
        cb_gecko_price=data.cb_gecko_price,
        cb_vwap=data.cb_vwap,
        cb_windfall_pct=data.cb_windfall_pct,
        cb_acc_pct=data.cb_acc_pct,
        cb_dist_pct=data.cb_dist_pct,
        cb_signals=data.cb_signals,
    )

    # ── master-scan 积分 ──
    master_map = {
        "DIAMOND": config.MASTER_DIAMOND,
        "RED":     config.MASTER_RED,
        "YELLOW":  config.MASTER_YELLOW,
    }
    r.master_score = master_map.get(data.master_signal, 0)

    # ── opus-scan 积分（正向吸筹 / 负向出货）──
    r.opus_score = round(data.opus_acc_conf * config.OPUS_ACC_SCALE
                         - data.opus_dist_conf * config.OPUS_DIST_SCALE, 2)

    # ── unified-scan 积分（吸筹方向 + 出货方向）──
    if data.unified_signal in config.UNIFIED_DIST_SCORE:
        r.unified_score = config.UNIFIED_DIST_SCORE[data.unified_signal]
    else:
        r.unified_score = config.UNIFIED_SCORE.get(data.unified_signal, 0)

    # ── whale-scan 积分 ──
    whale_map = {
        "HIGH":   config.WHALE_HIGH,
        "MEDIUM": config.WHALE_MEDIUM,
        "LOW":    config.WHALE_LOW,
    }
    r.whale_score = whale_map.get(data.whale_level, 0)

    # ── cost-basis-scan 积分 ──
    r.cb_score = config.CB_SCORE.get(data.cb_verdict, 0)

    # ── master/unified DIAMOND 信号去重（仅 DIAMOND 同源去重）──
    if r.master_signal == "DIAMOND" and r.unified_signal == "DIAMOND":
        r.unified_score = round(r.unified_score * 0.5, 2)

    # ── 出货方向 master 抑制 ──
    # 当 opus/unified 明确出货时，master 正分不应抵消出货积分
    is_dist_signal = (
        data.opus_verdict == "SLOW_DISTRIBUTION"
        or data.unified_signal in ("SLOW_DIST", "WHALE_DUMP")
        or data.cb_verdict in ("DEATH_SPIRAL", "LIQUIDITY_CRISIS")
    )
    if is_dist_signal and r.master_score > 0:
        r.master_score = 0

    # ── 综合积分 ──
    r.meta_score = round(
        r.master_score + r.opus_score + r.unified_score + r.whale_score + r.cb_score, 2
    )

    # ── 裁决 ──
    if r.meta_score >= config.META_ACC_THRESHOLD:
        r.meta_verdict = "ACC"
    elif r.meta_score <= config.META_DIST_THRESHOLD:
        r.meta_verdict = "DIST"
    else:
        r.meta_verdict = "NEUTRAL"

    # ── 生命周期阶段 ──
    r.stage = _infer_stage(r, data)

    return r


def _infer_stage(r: MetaResult, data: TokenEngineData) -> str:
    """推断代币生命周期阶段"""
    # 极度控盘：master DIAMOND + whale HIGH
    if r.master_signal == "DIAMOND" and r.whale_level == "HIGH":
        return "CONTROLLED"

    # 出货末期
    if r.cb_verdict in ("DEATH_SPIRAL", "LIQUIDITY_CRISIS"):
        return "DISTRIBUTING"

    # 主动派发
    if r.meta_verdict == "DIST":
        return "DISTRIBUTING"

    # 吸筹阶段
    if r.meta_verdict == "ACC":
        if r.master_signal == "DIAMOND":
            return "CONTROLLED"
        return "ACCUMULATING"

    # 有 master 信号但综合未达阈值 → 观察
    if r.master_signal in ("RED", "YELLOW"):
        return "WATCHLIST"

    return "NEUTRAL"


def run_arbitration(all_data: list[TokenEngineData]) -> tuple[list[MetaResult], list[MetaResult]]:
    """批量仲裁，返回 (acc排行, dist预警)"""
    results = [arbitrate(d) for d in all_data]

    acc_list  = sorted(
        [r for r in results if r.meta_verdict == "ACC"],
        key=lambda r: r.meta_score, reverse=True
    )
    dist_list = sorted(
        [r for r in results if r.meta_verdict == "DIST"],
        key=lambda r: r.meta_score
    )
    return acc_list, dist_list
