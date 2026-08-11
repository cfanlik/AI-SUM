"""
cost-basis-scan 裁决引擎
7 信号评分 + 三层裁决（跃迁 → 成本带 → 通用）
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import config
from cost_profiler import CostProfile
from gravity_tracker import GravityResult, WatchlistTransition
from db_loader import GeckoSnapshot


@dataclass
class Evidence:
    code: str
    name: str
    weight: int
    matched: bool
    detail: str
    dimension: str   # ACC / DIST / STRUCT


@dataclass
class VerdictResult:
    chain: str
    token_address: str
    token_symbol: str

    # 成本画像
    gecko_price: float = 0.0
    vwap: float = 0.0
    cost_gravity: float = 0.0
    cost_cv: float = 0.0
    cost_holders_count: int = 0

    # 四成本带占比
    deep_underwater_pct: float = 0.0
    shallow_underwater_pct: float = 0.0
    profit_zone_pct: float = 0.0
    windfall_pct: float = 0.0

    # 信号
    acc_pct: float = 0.0
    dist_pct: float = 0.0
    struct_pct: float = 0.0
    evidence: list[Evidence] = field(default_factory=list)

    # 裁决
    verdict: str = "NEUTRAL"
    verdict_detail: str = ""
    verdict_layer: str = ""          # TRANSITION / COST_COMBO / GENERAL

    # 重心漂移
    gravity_drift_ratio: float = 0.0
    gravity_drift_label: str = ""

    # watchlist
    watchlist_ref: str = ""
    only_buy_cnt: int = 0
    only_buy_pct: float = 0.0
    only_buy_hold_pct: float = 0.0
    sell_under_1_cnt: int = 0
    sell_under_1_pct: float = 0.0
    sell_under_1_hold_pct: float = 0.0
    sell_under_3_cnt: int = 0
    sell_under_3_pct: float = 0.0
    sell_under_3_hold_pct: float = 0.0

    # Gecko 补充
    lp_usd: float = 0.0
    vl_ratio: float = 0.0

    # 门控
    gate_skipped: str = ""

    @property
    def triggered_signals(self) -> str:
        return ",".join(e.code for e in self.evidence if e.matched)


def evaluate(
    profile: CostProfile,
    gecko: Optional[GeckoSnapshot],
    gravity: GravityResult,
    transition: WatchlistTransition,
    token_symbol: str = "?",
) -> VerdictResult:
    """三层裁决主入口"""
    vr = VerdictResult(
        chain=profile.chain,
        token_address=profile.token_address,
        token_symbol=token_symbol,
        gecko_price=profile.gecko_price,
        vwap=profile.vwap,
        cost_gravity=gravity.current_gravity,
        cost_cv=profile.cost_cv,
        cost_holders_count=profile.cost_holders_count,
        gravity_drift_ratio=gravity.drift_ratio,
        gravity_drift_label=gravity.drift_label,
    )

    if gecko:
        vr.lp_usd = gecko.reserve_usd
        vr.vl_ratio = gecko.vl_ratio

    if transition.has_history:
        vr.watchlist_ref = f"{transition.past_signal_level}/{transition.past_trigger}"

    # ── 四成本带占比 ──
    total_hold = (profile.deep_underwater.hold_pct_sum
                  + profile.shallow_underwater.hold_pct_sum
                  + profile.profit_zone.hold_pct_sum
                  + profile.windfall_zone.hold_pct_sum)
    if total_hold > 0:
        vr.deep_underwater_pct = round(profile.deep_underwater.hold_pct_sum / total_hold * 100, 1)
        vr.shallow_underwater_pct = round(profile.shallow_underwater.hold_pct_sum / total_hold * 100, 1)
        vr.profit_zone_pct = round(profile.profit_zone.hold_pct_sum / total_hold * 100, 1)
        vr.windfall_pct = round(profile.windfall_zone.hold_pct_sum / total_hold * 100, 1)

    # ── 门控检查 ──
    lp = gecko.reserve_usd if gecko else 0
    vol = gecko.volume_24h if gecko else 0
    vl = gecko.vl_ratio if gecko else 0

    if lp > 0 and lp < config.G3_LP_VETO_USD:
        vr.gate_skipped = f"G3 LP否决: ${lp:,.0f}"
        vr.verdict = "NEUTRAL"
        vr.verdict_detail = vr.gate_skipped
        return vr

    if vl < config.G4_DEAD_POOL_VL and vol < config.G4_DEAD_POOL_VOL and lp > 0:
        vr.gate_skipped = "G4 死池"
        vr.verdict = "NEUTRAL"
        vr.verdict_detail = vr.gate_skipped
        return vr

    # ── 7 信号评估 ──
    _evaluate_signals(vr, profile, gecko)

    # ── 第一层: watchlist 状态跃迁 ──
    if transition.transition_verdict:
        vr.verdict_layer = "TRANSITION"
        if transition.transition_verdict == "DEATH_SPIRAL":
            vr.verdict = "DEATH_SPIRAL"
            vr.verdict_detail = f"历史{transition.past_signal_level} + 暴利出逃 → 终极雪崩"
        elif transition.transition_verdict == "SQUEEZE_ACC":
            # SQUEEZE_ACC 置信度分层
            c1 = _signal_hit(vr, "C1")
            c2 = _signal_hit(vr, "C2")
            has_drift_history = gravity.drift_label not in ("FIRST_SCAN", "NO_DATA", "")
            if c1 and c2 and has_drift_history:
                vr.verdict = "SQUEEZE_ACC_HIGH"
                vr.verdict_detail = "历史RED + 水下加仓 + 一致行动人 + 有历史对比"
            elif c1 and (c2 or has_drift_history):
                vr.verdict = "SQUEEZE_ACC_MED"
                vr.verdict_detail = "历史RED + 水下加仓 + " + ("一致行动人" if c2 else "有历史对比")
            else:
                vr.verdict = "SQUEEZE_ACC_LOW"
                vr.verdict_detail = "历史RED + 水下加仓"
        return vr

    # ── 第二层: 成本带组合裁决 ──
    c1 = _signal_hit(vr, "C1")
    c2 = _signal_hit(vr, "C2")
    c3 = _signal_hit(vr, "C3")
    c4 = _signal_hit(vr, "C4")
    c5 = _signal_hit(vr, "C5")
    c6 = _signal_hit(vr, "C6")
    c7 = _signal_hit(vr, "C7")

    if c1 and c2:
        vr.verdict = "STEALTH_ACC"
        vr.verdict_detail = "水下加仓 + 一致行动人"
        vr.verdict_layer = "COST_COMBO"
        return vr

    if c2 and c3:
        vr.verdict = "IRON_HOLD"
        vr.verdict_detail = "一致行动人 + 暴利不卖"
        vr.verdict_layer = "COST_COMBO"
        return vr

    if c4 and c6:
        vr.verdict = "LIQUIDITY_CRISIS"
        vr.verdict_detail = f"暴利出逃 + LP=${vr.lp_usd:,.0f}"
        vr.verdict_layer = "COST_COMBO"
        return vr

    if c4 and vr.dist_pct >= config.VERDICT_DIST_MIN:
        vr.verdict = "PROFIT_EXIT"
        vr.verdict_detail = f"暴利出逃 DIST={vr.dist_pct:.0f}%"
        vr.verdict_layer = "COST_COMBO"
        return vr

    if c5 and c7:
        vr.verdict = "BAG_PASSING"
        vr.verdict_detail = "高位派发 + 成本倒挂"
        vr.verdict_layer = "COST_COMBO"
        return vr

    # ── C9 独立裁决: 深度套牢协同持仓 ──
    c9 = _signal_hit(vr, "C9")
    if c9 and c2:
        vr.verdict = "STEALTH_ACC"
        vr.verdict_detail = "深度套牢 + 成本聚集 → 一致行动人控盘"
        vr.verdict_layer = "COST_COMBO"
        return vr

    if c9:
        vr.verdict = "COORDINATED_HOLD"
        vr.verdict_detail = "深度套牢协同持仓 → 拉升动机强"
        vr.verdict_layer = "COST_COMBO"
        return vr

    # ── 第三层: 通用方向裁决 ──
    if vr.acc_pct >= config.VERDICT_ACC_MIN:
        vr.verdict = "ACCUMULATING"
        vr.verdict_detail = f"ACC={vr.acc_pct:.0f}%"
        vr.verdict_layer = "GENERAL"
    elif vr.dist_pct >= config.VERDICT_DIST_MIN:
        vr.verdict = "DISTRIBUTING"
        vr.verdict_detail = f"DIST={vr.dist_pct:.0f}%"
        vr.verdict_layer = "GENERAL"
    else:
        vr.verdict = "NEUTRAL"
        vr.verdict_detail = f"ACC={vr.acc_pct:.0f}%/DIST={vr.dist_pct:.0f}%"
        vr.verdict_layer = "GENERAL"

    return vr


def _evaluate_signals(vr: VerdictResult, profile: CostProfile, gecko: Optional[GeckoSnapshot]):
    """评估 8 个信号"""

    # ── C1: 水下逆势加仓 (ACC, weight=7) ──
    c1_hit = profile.underwater_adding_count >= 2
    # 快照间距 > 48h 降权
    c1_degraded = False
    if c1_hit and profile.snapshot_gap_hours > config.SNAPSHOT_GAP_HOURS_MAX:
        c1_degraded = True
    c1_detail = f"水下区加仓={profile.underwater_adding_count}/{profile.underwater_adding_total}"
    if c1_degraded:
        c1_detail += f" ⚠间距{profile.snapshot_gap_hours:.0f}h(降权)"
    vr.evidence.append(Evidence(
        code="C1", name="水下逆势加仓", weight=config.C1_WEIGHT if not c1_degraded else config.C1_WEIGHT // 2,
        matched=c1_hit, dimension="ACC",
        detail=c1_detail,
    ))

    # ── C2: 成本聚集 (ACC, weight=6) ──
    c2_hit = (profile.cost_cv < config.C2_CV_THRESHOLD
              and profile.cost_holders_count >= 5)
    vr.evidence.append(Evidence(
        code="C2", name="成本聚集", weight=config.C2_WEIGHT,
        matched=c2_hit, dimension="ACC",
        detail=f"CV={profile.cost_cv:.3f} (阈值<{config.C2_CV_THRESHOLD})",
    ))

    # ── C3: 暴利区坚定持有 (ACC, weight=5) ──
    windfall_total = profile.windfall_zone.holder_count
    windfall_no_sell = sum(1 for h in profile.windfall_zone.holders if h.sell_cnt == 0)
    windfall_diamond_pct = (windfall_no_sell / windfall_total * 100) if windfall_total > 0 else 0
    c3_hit = windfall_diamond_pct >= config.C3_DIAMOND_HOLD_PCT and windfall_total >= 3
    vr.evidence.append(Evidence(
        code="C3", name="暴利区坚定持有", weight=config.C3_WEIGHT,
        matched=c3_hit, dimension="ACC",
        detail=f"暴利区不卖={windfall_no_sell}/{windfall_total} ({windfall_diamond_pct:.0f}%)",
    ))

    # ── C4: 暴利出逃预警 (DIST, weight=7) ──
    c4_hit = profile.windfall_selling_count >= 1
    vr.evidence.append(Evidence(
        code="C4", name="暴利出逃", weight=config.C4_WEIGHT,
        matched=c4_hit, dimension="DIST",
        detail=f"暴利区出逃={profile.windfall_selling_count}个",
    ))

    # ── C5: 高位派发 (DIST, weight=5) ──
    c5_hit = (profile.new_at_market_count >= 3
              and profile.old_whale_declining)
    vr.evidence.append(Evidence(
        code="C5", name="高位派发", weight=config.C5_WEIGHT,
        matched=c5_hit, dimension="DIST",
        detail=f"新地址成本≈现价={profile.new_at_market_count}, 老鲸减仓={'是' if profile.old_whale_declining else '否'}",
    ))

    # ── C6: 流动性危机 (STRUCT, weight=5) ──
    lp = gecko.reserve_usd if gecko else 0
    c6_hit = (vr.windfall_pct > config.C6_WINDFALL_PCT_MIN
              and 0 < lp < config.C6_LP_MAX_USD)
    vr.evidence.append(Evidence(
        code="C6", name="流动性危机", weight=config.C6_WEIGHT,
        matched=c6_hit, dimension="STRUCT",
        detail=f"暴利区={vr.windfall_pct:.1f}% LP=${lp:,.0f}",
    ))

    # ── C7: 成本-持仓倒挂 (STRUCT, weight=4) ──
    c7_hit = profile.cost_inverted
    vr.evidence.append(Evidence(
        code="C7", name="成本倒挂", weight=config.C7_WEIGHT,
        matched=c7_hit, dimension="STRUCT",
        detail=f"Top5 VWAP=${profile.top5_vwap:.6f} vs Tail VWAP=${profile.tail_vwap:.6f}",
    ))

    # ── C8: 暴利区 48h 净流出 (DIST, weight=4) ──
    c8_hit = False
    c8_pct = 0.0
    if profile.windfall_total_hold > 0:
        c8_pct = profile.windfall_48h_net_out / profile.windfall_total_hold * 100
        c8_hit = c8_pct > config.C8_NETFLOW_PCT
    vr.evidence.append(Evidence(
        code="C8", name="暴利区48h净流出", weight=config.C8_WEIGHT,
        matched=c8_hit, dimension="DIST",
        detail=f"净流出={c8_pct:.1f}%(阈值>{config.C8_NETFLOW_PCT}%)",
    ))


    # ── C9: 深度套牢协同持仓 (ACC, weight=6) ──
    # 大量地址深度套牢且成本高度集中 → 一致行动人控盘持仓
    c9_hit = False
    c9_detail = ""
    if profile.cost_holders_count >= config.C9_MIN_HOLDERS:
        median_cost = profile.vwap  # 使用 VWAP 作为中位成本近似
        underwater_ratio = median_cost / profile.gecko_price if profile.gecko_price > 0 else 0
        underwater_pct = vr.deep_underwater_pct + vr.shallow_underwater_pct
        c9_hit = (underwater_ratio >= config.C9_UNDERWATER_RATIO
                  and underwater_pct >= config.C9_UNDERWATER_PCT
                  and profile.cost_cv < config.C9_CV_THRESHOLD)
        c9_detail = (f"成本/现价={underwater_ratio:.1f}x 水下={underwater_pct:.0f}% "
                     f"CV={profile.cost_cv:.3f} holders={profile.cost_holders_count}")
    else:
        c9_detail = f"holders={profile.cost_holders_count} < {config.C9_MIN_HOLDERS}"
    vr.evidence.append(Evidence(
        code="C9", name="深度套牢协同持仓", weight=config.C9_WEIGHT,
        matched=c9_hit, dimension="ACC",
        detail=c9_detail,
    ))

    # ── 计算 ACC/DIST/STRUCT 百分比 ──
    acc_hit = sum(e.weight for e in vr.evidence if e.matched and e.dimension == "ACC")
    dist_hit = sum(e.weight for e in vr.evidence if e.matched and e.dimension == "DIST")
    struct_hit = sum(e.weight for e in vr.evidence if e.matched and e.dimension == "STRUCT")

    vr.acc_pct = round(acc_hit / config.ACC_WEIGHT_TOTAL * 100, 1) if config.ACC_WEIGHT_TOTAL > 0 else 0
    vr.dist_pct = round(dist_hit / config.DIST_WEIGHT_TOTAL * 100, 1) if config.DIST_WEIGHT_TOTAL > 0 else 0
    vr.struct_pct = round(struct_hit / config.STRUCT_WEIGHT_TOTAL * 100, 1) if config.STRUCT_WEIGHT_TOTAL > 0 else 0


def _signal_hit(vr: VerdictResult, code: str) -> bool:
    """检查指定信号是否命中"""
    return any(e.code == code and e.matched for e in vr.evidence)
