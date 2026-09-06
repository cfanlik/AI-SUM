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

    # ── 新增: 新激活地址与突击建仓集群指标 ──
    fresh_wallet_score: float = 0.0
    sybil_pattern:      str   = "REGULAR"   # DUAL_RESONANCE / FRESH_SYBIL / HOP2_PENETRATION / REGULAR
    fresh_1_7d_count:   int = 0
    fresh_1d_count:     int = 0
    fresh_2d_count:     int = 0
    fresh_3d_count:     int = 0
    fresh_4_7d_count:   int = 0
    fresh_1_7d_hold_pct:float = 0.0

    # ── 新增: P2 出货风控结果透传 ──
    dump_penalty:       float = 0.0             # 累计出货风控扣分
    dump_reasons:       str   = ""              # 出货与风控原因摘要
    price_now_ret:      float | None = None     # 信号首发至今收益率
    hold_delta_72h_pct: float | None = None     # 72h 持仓变动
    acc_count_latest:   int = 0                 # 最新吸筹地址数量


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

    # hop2 加分 (含吸筹总数 >= 30 防小样本门禁与三档分级标准)
    acc_count = getattr(t, "acc_count_latest", 0)
    if acc_count >= 30:
        if hop2_pct > 0.20:
            res.hop2_score = 1.50
        elif hop2_pct > 0.10:
            res.hop2_score = 1.00
        elif hop2_pct > 0.05:
            res.hop2_score = 0.50
        else:
            res.hop2_score = 0.00
    else:
        res.hop2_score = 0.00

    # 透传新激活地址分箱指标
    res.fresh_1_7d_count = getattr(t, "fresh_1_7d_count", 0)
    res.fresh_1d_count = getattr(t, "fresh_1d_count", 0)
    res.fresh_2d_count = getattr(t, "fresh_2d_count", 0)
    res.fresh_3d_count = getattr(t, "fresh_3d_count", 0)
    res.fresh_4_7d_count = getattr(t, "fresh_4_7d_count", 0)
    res.fresh_1_7d_hold_pct = getattr(t, "fresh_1_7d_hold_pct", 0.0)

    # 1.6 新激活地址突击建仓调节分 (双通道门禁：通道A高控盘老鼠仓[>=5号且>=10%]；通道B多号微量突击集群[>=10号且>=2%])
    if res.fresh_1_7d_count >= 5 and res.fresh_1_7d_hold_pct >= 10.0:
        if res.fresh_1_7d_hold_pct >= 30.0 and res.fresh_1_7d_count >= 10:
            res.fresh_wallet_score = 1.50
        elif res.fresh_1_7d_hold_pct >= 20.0 and res.fresh_1_7d_count >= 5:
            res.fresh_wallet_score = 1.00
        else:
            res.fresh_wallet_score = 0.50
    elif res.fresh_1_7d_count >= 10 and res.fresh_1_7d_hold_pct >= 2.0:
        res.fresh_wallet_score = 0.50
    else:
        res.fresh_wallet_score = 0.00

    # 1.7 筹码三态形态分类器 (Dual-Resonance vs Fresh-Sybil vs Hop2-Penetration)
    has_hop2 = (res.hop2_score > 0 or hop2_pct > 0.05)
    has_fresh = (res.fresh_wallet_score > 0) or (res.fresh_1_7d_count >= 3 and res.fresh_1_7d_hold_pct >= 5.0) or (res.fresh_1_7d_count >= 10 and res.fresh_1_7d_hold_pct >= 2.0)
    
    if has_hop2 and has_fresh:
        res.sybil_pattern = "DUAL_RESONANCE"
        # 双共振形态：资金穿透链路与新号建仓产生协同共振，赋予拓扑协同增益 +0.5分 (封顶 2.0)
        res.fresh_wallet_score = round(min(2.0, res.fresh_wallet_score + 0.50), 2)
    elif has_fresh:
        res.sybil_pattern = "FRESH_SYBIL"
    elif has_hop2:
        res.sybil_pattern = "HOP2_PENETRATION"
    else:
        res.sybil_pattern = "REGULAR"

    # 2. 汇总总分
    raw_total = res.master_score + res.opus_score + res.unified_score + res.whale_score + res.cb_score + res.hop2_score + res.fresh_wallet_score
    res.meta_score = round(raw_total, 2)
    res.engine_hits = sum(1 for s in [res.master_score, res.opus_score, res.unified_score, res.whale_score, res.cb_score] if s > 0)

    # ── 2.1 P2 三维出货风控与看涨纠偏雷达 ──
    _dump_penalty = 0.0
    _dump_reasons = []

    # ── 维度 A: 价格偏离连续平滑惩罚算子 (Continuous Piecewise Linear: -30% ~ -70% -> 1.0 ~ 3.0) ──
    if t.price_now_ret is not None and t.price_now_ret <= -30.0:
        p_loss = abs(t.price_now_ret)
        ratio = min(1.0, (p_loss - 30.0) / (70.0 - 30.0))
        p_pen = round(1.0 + 2.0 * ratio, 2)
        _dump_penalty += p_pen
        if p_pen >= 2.5:
            _dump_reasons.append(f"信号暴跌巨亏({t.price_now_ret:+.1f}%)")
        else:
            _dump_reasons.append(f"严重价格偏离({t.price_now_ret:+.1f}%)")

    # ── 维度 B: 72h 同源队列持仓衰减连续平滑算子 (-5% ~ -15% -> 0.5 ~ 1.5，防误杀 acc_count >= 10) ──
    if t.hold_delta_72h_pct is not None and t.acc_count_latest >= 10 and t.hold_delta_72h_pct <= -5.0:
        h_loss = abs(t.hold_delta_72h_pct)
        ratio = min(1.0, (h_loss - 5.0) / (15.0 - 5.0))
        c_pen = round(0.5 + 1.0 * ratio, 2)
        _dump_penalty += c_pen
        if c_pen >= 1.3:
            _dump_reasons.append(f"72h持仓断崖抛售({t.hold_delta_72h_pct:+.1f}%)")
        elif c_pen >= 0.9:
            _dump_reasons.append(f"72h持仓显著流失({t.hold_delta_72h_pct:+.1f}%)")
        else:
            _dump_reasons.append(f"72h持仓轻度松动({t.hold_delta_72h_pct:+.1f}%)")

    # ── 维度 C: Opus 出货置信度连续平滑算子 (30% ~ 60% -> 0.5 ~ 1.5) ──
    if t.opus_verdict in ("SLOW_DISTRIBUTION", "DISTRIBUTING") and t.opus_dist_conf >= 30.0:
        ratio = min(1.0, (t.opus_dist_conf - 30.0) / (60.0 - 30.0))
        o_pen = round(0.5 + 1.0 * ratio, 2)
        _dump_penalty += o_pen
        _dump_reasons.append(f"opus出货({t.opus_verdict},{t.opus_dist_conf:.0f}%)")

    # ── 维度 D: 诱多套牢背离算子 (Bull-Trap Divergence, BDC, 零硬编码) ──
    # 当局部持仓未流失甚至微增(ΔH >= 0)，但二级市场价格严重破位(ΔP <= -50%)且伴随出货时
    if t.price_now_ret is not None and t.price_now_ret <= -50.0:
        h_gain = max(0.0, t.hold_delta_72h_pct or 0.0)
        p_loss_ratio = min(1.0, abs(t.price_now_ret) / 100.0)
        opus_factor = 0.5 + 0.5 * (t.opus_dist_conf / 100.0 if t.opus_verdict in ("SLOW_DISTRIBUTION", "DISTRIBUTING") else 0.0)
        bdc = round(p_loss_ratio * (1.0 - math.exp(-h_gain / 10.0) if h_gain > 0 else 0.5) * opus_factor, 2)
        if bdc >= 0.35 and t.hold_delta_72h_pct is not None and t.hold_delta_72h_pct >= 0:
            _dump_reasons.append(f"诱多套牢背离(BDC={bdc:.2f},局部持币{t.hold_delta_72h_pct:+.1f}% vs 破位{t.price_now_ret:+.1f}%)")

    if _dump_penalty > 0:
        res.meta_score = round(res.meta_score - _dump_penalty, 2)
        res.dump_penalty = round(_dump_penalty, 2)
        res.dump_reasons = "; ".join(_dump_reasons)

    # 透传偏离度数据供报告层展示
    res.price_now_ret = t.price_now_ret
    res.hold_delta_72h_pct = t.hold_delta_72h_pct
    res.acc_count_latest = t.acc_count_latest

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
