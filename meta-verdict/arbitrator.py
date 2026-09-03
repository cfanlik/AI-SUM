"""
meta-verdict 仲裁引擎 (Master Cockpit 双轨分流与风控增强版)
5 引擎投票 → 加权积分 → 统一排名 + 生命周期状态机 + 轧空/真金双轨分流
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
import config
import sqlite3
from collector import TokenEngineData


@dataclass
class MetaResult:
    chain: str
    token_address: str
    token_symbol: str

    meta_score: float = 0.0
    meta_score_smooth: float = 0.0
    meta_verdict: str = "NEUTRAL"   # ACC / DIST / NEUTRAL
    engine_hits: int = 0

    # 各引擎贡献分
    master_score:  float = 0.0
    opus_score:    float = 0.0
    unified_score: float = 0.0
    whale_score:   float = 0.0
    cb_score:      float = 0.0
    hop2_score:    float = 0.0

    # 原始数据透传
    master_signal:  str   = ""
    opus_verdict:   str   = ""
    unified_signal: str   = ""
    whale_level:    str   = ""
    cb_verdict:     str   = ""

    # 价格与成本数据（来自 cost-basis-scan & gecko）
    cb_gecko_price: float = 0.0
    cb_vwap:        float = 0.0
    cb_windfall_pct:float = 0.0
    cb_acc_pct:     float = 0.0
    cb_dist_pct:    float = 0.0
    cb_signals:     str   = ""

    # 深度与流动性透传
    reserve_usd:     float = 0.0
    volume_24h:      float = 0.0
    vl_ratio:        float = 0.0
    market_cap_usd:  float = 0.0
    fdv_usd:         float = 0.0

    # 筹码与 CEX 渗透透传
    cex_hold_pct:       float = 0.0
    cex_delta_pct:      float = 0.0
    institutional_hold: float = 0.0
    top10_hold:         float = 0.0
    lp_locked_ratio:    float = 0.0

    # 5 轮时序拟合特征
    series_trajectory: str   = ""
    series_std:        float = 0.0
    series_desc:       str   = ""

    # 生命周期与置信度梯队 (完全独立隔离)
    stage: str = ""   # ACCUMULATING / CONTROLLED / DISTRIBUTING / WATCHLIST / NEUTRAL
    confidence_tier: str = "L3-Watch"   # L1-Alpha / L1-Squeeze / L1-Special / L2-Bet / L3-Watch / DENIED
    resilience_index: float = 0.0       # 历史抗跌韧性原始分
    resilience_norm: float = 0.5        # Sigmoid [0, 1] 稳健平滑分

    # ── 新增: P2 出货风控结果透传 ──
    dump_penalty:       float = 0.0             # 累计出货风控扣分
    dump_reasons:       str   = ""              # 出货与风控原因摘要
    price_now_ret:      float | None = None     # 信号首发至今收益率
    hold_delta_72h_pct: float | None = None     # 72h 持仓变动


def calculate_resilience_norm(raw_val: float | None) -> float | None:
    """Sigmoid 稳健归一化，消除离群极值影响，映射至 [0.0, 1.0]"""
    if raw_val is None:
        return None
    try:
        clipped = max(-2000.0, min(2000.0, float(raw_val)))
        return round(1.0 / (1.0 + math.exp(-clipped / 200.0)), 4)
    except Exception:
        return 0.5


def get_prev_consec_acc(conn: sqlite3.Connection, chain: str, token_address: str) -> int:
    """查询该代币在上一轮运行前的连续 ACC 轮次"""
    if conn is None:
        return 0
    try:
        cursor = conn.execute("""
            SELECT meta_verdict FROM meta_snapshots
            WHERE chain = ? AND lower(token_address) = lower(?)
            ORDER BY scan_time DESC LIMIT 30
        """, (chain, token_address.lower()))
        rows = cursor.fetchall()
        consec = 0
        for r in rows:
            if r["meta_verdict"] == "ACC":
                consec += 1
            else:
                break
        return consec
    except Exception:
        return 0


def determine_confidence_tier(token: TokenEngineData, score: float, hits: int, consecutive_acc: int) -> str:
    """
    置信度等级评定算法 (含 L1-Alpha vs L1-Squeeze 自动分流及 LP 锁仓风控)
    """
    # 门禁 0: 命中出货判定或严重负分，物理阻断降为 DENIED (防止 DIST + L2-Bet 矛盾)
    if score <= -2.0:
        return "DENIED"

    # 门禁 1: 极端低流动性或撤池死池拦截
    if token.reserve_usd > 0 and token.reserve_usd < 10000:
        return "DENIED"

    # 门禁 2: 极度派发/暴利狂抛拦截
    if token.cb_windfall_pct > 80.0 and token.cex_delta_pct > 30.0:
        return "DENIED"

    # ── L1 顶级共振标的判定 (得分 >= 7.0 且多引擎共振) ──
    if score >= 7.0:
        # 轧空分流器 (Squeeze Diverter)
        if token.vl_ratio > 10.0 or token.cex_delta_pct > 20.0:
            return "L1-Squeeze"
        
        # 机构高控盘特例
        if token.institutional_hold >= 90.0 and token.vl_ratio < 0.1:
            return "L1-Special"

        # 默认优质真金吸筹
        return "L1-Alpha"

    # ── L2 潜力吸筹梯队 ──
    if score >= 4.5 or (score >= 3.0 and hits >= 2):
        if token.vl_ratio > 15.0:
            return "L2-Speculative"
        return "L2-Bet"

    # ── L3 观察池 ──
    return "L3-Watch"


def arbitrate(token: TokenEngineData, hop2_pct: float = 0.0, conn: sqlite3.Connection = None, scan_time: str = "") -> MetaResult:
    """单代币加权积分仲裁 (兼容 run.py 调用签名)"""
    t = token
    res = MetaResult(
        chain=t.chain,
        token_address=t.token_address,
        token_symbol=t.token_symbol,
        master_signal=t.master_signal,
        opus_verdict=t.opus_verdict,
        unified_signal=t.unified_signal,
        whale_level=t.whale_level,
        cb_verdict=t.cb_verdict,
        cb_gecko_price=t.cb_gecko_price,
        cb_vwap=t.cb_vwap,
        cb_windfall_pct=t.cb_windfall_pct,
        cb_acc_pct=t.cb_acc_pct,
        cb_dist_pct=t.cb_dist_pct,
        cb_signals=t.cb_signals,
        reserve_usd=t.reserve_usd,
        volume_24h=t.volume_24h,
        vl_ratio=t.vl_ratio,
        market_cap_usd=t.market_cap_usd,
        fdv_usd=t.fdv_usd,
        cex_hold_pct=t.cex_hold_pct,
        cex_delta_pct=t.cex_delta_pct,
        institutional_hold=t.institutional_hold,
        top10_hold=t.top10_hold,
        lp_locked_ratio=t.lp_locked_ratio,
    )

    # 1. 计算各引擎贡献分 (依据 config 权重)
    # master-scan
    m_weights = {"DIAMOND": 4.0, "RED": 2.0, "YELLOW": 1.0}
    res.master_score = m_weights.get(t.master_signal, 0.0)

    # opus-scan
    if t.opus_verdict == "ACCUMULATING":
        res.opus_score = round(t.opus_acc_conf / 50.0, 2)
    elif t.opus_verdict in ("SLOW_DISTRIBUTION", "DISTRIBUTING"):
        res.opus_score = -round(t.opus_dist_conf / 50.0, 2)

    # unified-scan
    u_weights = {"DIAMOND": 4.0, "RED": 2.0, "YELLOW": 1.0, "WHALE_DUMP": -3.0, "SLOW_DIST": -2.0}
    res.unified_score = u_weights.get(t.unified_signal, 0.0)

    # whale-scan
    w_weights = {"HIGH": 2.0, "MEDIUM": 1.0, "LOW": 0.0, "CLEAN": 0.0}
    res.whale_score = w_weights.get(t.whale_level, 0.0)

    # cost-basis-scan
    cb_weights = {"STRONG_ACC": 2.0, "ACC": 1.0, "NEUTRAL": 0.0, "DIST": -1.5, "STRONG_DIST": -3.0}
    res.cb_score = cb_weights.get(t.cb_verdict, 0.0)

    # hop2 加分
    if hop2_pct >= 0.5:
        res.hop2_score = round(hop2_pct * 1.5, 2)

    # 2. 汇总总分
    raw_total = res.master_score + res.opus_score + res.unified_score + res.whale_score + res.cb_score + res.hop2_score
    res.meta_score = round(raw_total, 2)
    res.engine_hits = sum(1 for s in [res.master_score, res.opus_score, res.unified_score, res.whale_score, res.cb_score] if s > 0)

    # ── 2.1 P2 三维出货风控与看涨纠偏雷达 ──
    _dump_penalty = 0.0
    _dump_reasons = []

    # 维度 A: opus-scan 出货判定联动
    if t.opus_verdict in ("SLOW_DISTRIBUTION", "DISTRIBUTING") and t.opus_dist_conf >= 50.0:
        _dump_penalty += 1.5
        _dump_reasons.append(f"opus出货({t.opus_verdict},{t.opus_dist_conf:.0f}%)")

    # 维度 B: 72h 吸筹持仓断崖衰减 (增加样本量 >= 10 防误杀门禁)
    if t.hold_delta_72h_pct is not None and t.hold_delta_72h_pct <= -15.0:
        if t.acc_count_latest >= 10:
            _dump_penalty += 1.5
            _dump_reasons.append(f"72h持仓衰减({t.hold_delta_72h_pct:+.1f}%)")

    # 维度 C: 信号首发至今价格严重偏离 (归零与暴跌惩罚)
    if t.price_now_ret is not None:
        if t.price_now_ret <= -70.0:
            _dump_penalty += 3.0
            _dump_reasons.append(f"信号暴跌巨亏({t.price_now_ret:+.1f}%)")
        elif t.price_now_ret <= -50.0:
            _dump_penalty += 2.0
            _dump_reasons.append(f"严重价格偏离({t.price_now_ret:+.1f}%)")

    if _dump_penalty > 0:
        res.meta_score = round(res.meta_score - _dump_penalty, 2)
        res.dump_penalty = round(_dump_penalty, 2)
        res.dump_reasons = "; ".join(_dump_reasons)

    # 透传偏离度数据供报告层展示
    res.price_now_ret = t.price_now_ret
    res.hold_delta_72h_pct = t.hold_delta_72h_pct

    # 3. 判定 meta_verdict & stage
    # 只要总分突破 -2.0 或 信号暴跌巨亏超 70%，一票否决强制评定为 DIST
    is_force_dist = (t.price_now_ret is not None and t.price_now_ret <= -70.0)
    if res.meta_score >= 3.0 and not is_force_dist:
        res.meta_verdict = "ACC"
        res.stage = "ACCUMULATING"
    elif res.meta_score <= -2.0 or is_force_dist:
        res.meta_verdict = "DIST"
        res.stage = "DISTRIBUTING"
    else:
        res.meta_verdict = "NEUTRAL"
        res.stage = "WATCHLIST" if res.engine_hits >= 1 else "NEUTRAL"

    # 4. 连续 ACC 轮次与置信度梯队评定 (DIST 绝对阻断为 DENIED)
    if res.meta_verdict == "DIST":
        res.confidence_tier = "DENIED"
    else:
        consec = get_prev_consec_acc(conn, t.chain, t.token_address)
        res.confidence_tier = determine_confidence_tier(t, res.meta_score, res.engine_hits, consec)

    return res


# 兼容别名
run_arbitration = arbitrate
