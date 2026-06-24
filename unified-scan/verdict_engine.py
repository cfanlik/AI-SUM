# ──────────────────────────────────────────────────────────
# 信号编码速查表 (Signal Code Reference)
# ──────────────────────────────────────────────────────────
# A1(DIAMOND/RED)  — BubbleMap 吸筹标签等级
# A2(YELLOW/RED)   — 二级吸筹指标（YELLOW=中等, RED=强）
# A4(CEX流出)      — 代币从 CEX 转出到链上 → 买入持有
# D1(CEX流入)      — 代币流入 CEX → 准备卖出
# D2(出货者)       — 检测到出货行为的地址
# D3(被动漂移)     — 持仓未变但价格下跌，被动承受亏损
# S1(极端集中)     — Top 地址持仓极度集中
# S2(M/L=Nx)       — 市值/流动性比 → 越高越脆弱
# ──────────────────────────────────────────────────────────

"""
unified-scan — 三维度评分 + 综合裁决引擎
ACC(22) + DIST(15) + STRUCT(11) → verdict
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import config


# 权重定义
WEIGHTS = {
    "A1": 8, "A2": 5, "A3": 4, "A4": 5, "A5": 4,   # ACC total=26 (新增 A5)
    "D1": 5, "D2": 4, "D3": 6,                       # DIST total=15
    "S1": 5, "S2": 4, "S3": 2, "S5": 5,              # STRUCT total=16 (新增 S5)
}
ACC_TOTAL = WEIGHTS["A1"] + WEIGHTS["A2"] + WEIGHTS["A3"] + WEIGHTS["A4"] + WEIGHTS["A5"]  # 26
DIST_TOTAL = WEIGHTS["D1"] + WEIGHTS["D2"] + WEIGHTS["D3"]                 # 15
STRUCT_TOTAL = WEIGHTS["S1"] + WEIGHTS["S2"] + WEIGHTS["S3"] + WEIGHTS["S5"]               # 16


@dataclass
class UnifiedResult:
    chain: str
    token_address: str
    token_symbol: str
    snap_count: int

    acc_score: float = 0.0
    dist_score: float = 0.0
    struct_risk: float = 0.0
    verdict: str = "NEUTRAL"

    # 信号触发
    triggered: list[str] = field(default_factory=list)
    signal_details: dict = field(default_factory=dict)

    # 关键指标快照
    acc_cnt: int = 0
    acc_hold_pct: float = 0.0
    dex_verified_pct: float = 0.0
    cex_hold_pct: float = 0.0
    cex_delta_pct: float = 0.0
    top2_hold: float = 0.0
    top10_hold: float = 0.0
    institutional_hold: float = 0.0
    hidden_whale_cnt: int = 0
    mcap_liq_ratio: float = 0.0
    lp_usd: float = 0.0
    vl_ratio: float = 0.0


def evaluate(
    diamond: dict,
    a2: dict, a3: dict,
    cex: dict,
    profiler: dict,
    drift: dict,
    concentration: dict,
    market: dict,
    token_info: dict,
) -> UnifiedResult:
    """三维度评分 + 综合裁决。"""
    r = UnifiedResult(
        chain=token_info["chain"],
        token_address=token_info["token_address"],
        token_symbol=token_info.get("token_symbol", "?"),
        snap_count=token_info.get("snap_count", 0),
    )

    # 填充指标快照
    r.acc_cnt = diamond.get("acc_count", 0)
    r.dex_verified_pct = diamond.get("dex_verified_pct", 0)
    r.institutional_hold = diamond.get("institutional_hold", 0)
    r.hidden_whale_cnt = diamond.get("hidden_whale_count", 0)
    r.cex_hold_pct = cex.get("cex_latest", 0)
    r.cex_delta_pct = cex.get("cex_delta_pct", 0)
    r.top2_hold = concentration.get("top2_hold", 0)
    r.top10_hold = concentration.get("top10_hold", 0)
    r.mcap_liq_ratio = market.get("mcap_liq_ratio", 0)
    r.lp_usd = market.get("pool_lp_usd") or 0 or 0
    r.vl_ratio = market.get("vl_ratio", 0)

    # ── 门控: G1 DEX 质量门 ──
    dex_gated = (r.dex_verified_pct < config.MIN_SIGNAL_DEX_PCT
                 and not diamond.get("triggered"))

    # ── ACC 维度 ──
    acc_hit = 0
    if diamond.get("triggered"):
        acc_hit += WEIGHTS["A1"]
        r.triggered.append("A1(DIAMOND)")
        r.signal_details["A1"] = diamond

    if not dex_gated:
        if a2.get("triggered"):
            acc_hit += WEIGHTS["A2"]
            r.triggered.append(f"A2({a2.get('level','')})")
            r.signal_details["A2"] = a2
        if a3.get("triggered"):
            acc_hit += WEIGHTS["A3"]
            r.triggered.append(f"A3({a3.get('level','')})")
            r.signal_details["A3"] = a3

    if cex.get("a4", {}).get("triggered"):
        acc_hit += WEIGHTS["A4"]
        r.triggered.append("A4(CEX流出)")
        r.signal_details["A4"] = cex["a4"]

    # 🔗 外挂注入: A5 DEX LP 强庄做市信号 (A5, 4分)
    try:
        import sys
        if "/opt/select-coin" not in sys.path:
            sys.path.insert(0, "/opt/select-coin")
        from onchain import dex_pool_analyzer
        lp_signals = dex_pool_analyzer.generate_resonance_signals(r.chain, r.token_address, r.pool_address)
        if lp_signals.get("A5_triggered"):
            acc_hit += WEIGHTS["A5"]
            r.triggered.append("A5(DEX LP注入)")
            r.signal_details["A5"] = lp_signals["metrics"]
    except Exception as e:
        pass

    r.acc_score = round(acc_hit / ACC_TOTAL * 100, 1)

    # ── DIST 维度 ──
    dist_hit = 0
    if cex.get("d1", {}).get("triggered"):
        dist_hit += WEIGHTS["D1"]
        r.triggered.append("D1(CEX流入)")
        r.signal_details["D1"] = cex["d1"]

    if profiler.get("triggered"):
        dist_hit += WEIGHTS["D2"]
        r.triggered.append("D2(出货者)")
        r.signal_details["D2"] = profiler

    if drift.get("triggered"):
        dist_hit += WEIGHTS["D3"]
        r.triggered.append("D3(被动漂移)")
        r.signal_details["D3"] = drift

    r.dist_score = round(dist_hit / DIST_TOTAL * 100, 1)

    # ── STRUCT 维度 ──
    struct_hit = 0
    if concentration.get("triggered"):
        struct_hit += WEIGHTS["S1"]
        r.triggered.append("S1(极端集中)")
        r.signal_details["S1"] = concentration

    if market.get("s2_triggered"):
        struct_hit += WEIGHTS["S2"]
        r.triggered.append(f"S2(M/L={r.mcap_liq_ratio}x)")
        r.signal_details["S2"] = {"mcap_liq_ratio": r.mcap_liq_ratio}

    # S3: 买卖人数比 → 同时作为ACC和DIST的辅助信号
    if market.get("s3_acc_triggered"):
        struct_hit += WEIGHTS["S3"]
        r.triggered.append("S3(买>卖)")
    elif market.get("s3_dist_triggered"):
        struct_hit += WEIGHTS["S3"]
        r.triggered.append("S3(卖>买)")

    # S4: V/L 换手效率标记（不计入 STRUCT 评分）
    if market.get("s4_vl_triggered"):
        r.triggered.append(f"S4(V/L={market.get('vl_ratio', 0)})")
        r.signal_details["S4"] = {"vl_ratio": market.get("vl_ratio", 0)}

    # 🔗 外挂注入: S5 DEX LP 恶意撤池抽毯风险 (S5, 5分)
    try:
        import sys
        if "/opt/select-coin" not in sys.path:
            sys.path.insert(0, "/opt/select-coin")
        from onchain import dex_pool_analyzer
        lp_signals = dex_pool_analyzer.generate_resonance_signals(r.chain, r.token_address, r.pool_address)
        if lp_signals.get("S5_triggered"):
            struct_hit += WEIGHTS["S5"]
            r.triggered.append("S5(DEX LP流失风险)")
            r.signal_details["S5"] = lp_signals["metrics"]
    except Exception as e:
        pass

    r.struct_risk = round(struct_hit / STRUCT_TOTAL * 100, 1)

    # ── G2: LP 流动性门控 ──
    if r.lp_usd > 0:
        if r.lp_usd < config.G2_LP_VETO_USD:
            r.triggered.append(f"G2(LP=${r.lp_usd:,.0f}|否决)")
            # LP < $10K → 否决所有信号
            r.acc_score = 0
            r.dist_score = 0
            r.struct_risk = 0
            r.verdict = "NEUTRAL"
            return r
        elif r.lp_usd < config.G2_LP_THIN_USD:
            r.triggered.append(f"G2(LP=${r.lp_usd:,.0f}|降级)")

    # ── G3: 死池检测 ──
    _vol = market.get("pool_volume_24h") or 0 or 0
    if r.vl_ratio < config.G3_DEAD_POOL_VL and _vol < config.G3_DEAD_POOL_VOL:
        r.triggered.append("G3(死池)")
        # 死池 → 否决 ACC 方向信号
        if r.acc_score > 0:
            r.acc_score = 0
            r.verdict = "NEUTRAL"
            return r

    # ── 综合裁决 ──
    r.verdict = _determine_verdict(r, diamond)

    # G2 降级后处理
    if r.lp_usd > 0 and r.lp_usd < config.G2_LP_THIN_USD:
        if r.verdict in ("DIAMOND", "STRONG_ACC"):
            r.verdict = "MODERATE_ACC"

    return r


def _determine_verdict(r: UnifiedResult, diamond: dict) -> str:
    if diamond.get("triggered"):
        return "DIAMOND"
    # D3(被动漂移) + S1(极端集中度) 联合触发 → 庄控出货
    has_d3 = "D3" in r.signal_details
    has_s1 = "S1" in r.signal_details
    if has_d3 and has_s1:
        return "WHALE_DUMP"
    if r.acc_score >= 60 and r.dist_score >= 50:
        return "MIXED"
    if r.dist_score >= 50 and r.struct_risk >= 60:
        return "WHALE_DUMP"
    if r.dist_score >= 50:
        return "SLOW_DISTRIBUTION"
    if r.acc_score >= 60:
        return "STRONG_ACC"
    if r.acc_score >= 30:
        return "MODERATE_ACC"
    return "NEUTRAL"

