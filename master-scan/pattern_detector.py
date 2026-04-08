"""
AI-SUM V8.2 — 行为模式识别引擎
四大核心模式：
  A — 地址聚合（Aggregation Pattern）
  B — 新鲸下场（Fresh Whale Pattern）
  C — 爆发前静默（Pre-Pump Silence）
  E — 钻石绞杀区（Diamond Squeeze）— V8.2 新增
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import config
from time_series_aligner import SnapshotDiff, TokenTimeSeries


# ============================================================
# 信号级别常量
# ============================================================

LEVEL_NONE     = None
LEVEL_YELLOW   = "YELLOW"
LEVEL_RED      = "RED"
LEVEL_CRITICAL = "CRITICAL"
LEVEL_EXTREME  = "EXTREME"
LEVEL_DIAMOND  = "DIAMOND"


# ============================================================
# 检测结果数据结构
# ============================================================

@dataclass
class PatternResult:
    chain: str
    token_address: str
    token_symbol: str
    snap_count: int
    latest_snapshot: str

    pattern_a_level: Optional[str] = None
    pattern_a_detail: dict = field(default_factory=dict)

    pattern_b_level: Optional[str] = None
    pattern_b_detail: dict = field(default_factory=dict)

    pattern_c_level: Optional[str] = None
    pattern_c_detail: dict = field(default_factory=dict)
    pattern_c_conditions_met: int = 0

    composite_level: Optional[str] = None
    triggered_patterns: list[str] = field(default_factory=list)

    # diff 关键指标
    roster_turnover_pct: float = 0.0
    hours_gap: float = 0.0
    gap_warning: bool = False
    new_acc_count: int = 0
    new_acc_only_buy: int = 0
    latest_only_buy_pct: float = 0.0
    acc_hold_new: float = 0.0
    delta_acc_hold: float = 0.0
    delta_acc_count: int = 0
    acc_count_new: int = 0

    # V8.2 字段
    institutional_hold_v8: float = 0.0
    hidden_whale_count: int = 0
    dex_verified_pct: float = 0.0

    @property
    def has_signal(self) -> bool:
        return self.composite_level is not None

    @property
    def is_red_or_above(self) -> bool:
        return self.composite_level in (LEVEL_RED, LEVEL_CRITICAL, LEVEL_EXTREME)


# ============================================================
# 模式 A — 地址聚合
# ============================================================

def detect_pattern_a(diff: SnapshotDiff) -> tuple[Optional[str], dict]:
    turnover = diff.roster_turnover_pct
    new_acc   = diff.new_acc_count
    old_acc   = diff.acc_count_old
    only_buy  = diff.new_acc_only_buy

    detail = {
        "roster_turnover_pct": round(turnover * 100, 1),
        "new_acc_count": new_acc,
        "old_acc_count": old_acc,
        "new_acc_only_buy": only_buy,
        "new_acc_avg_score": diff.new_acc_avg_score,
    }

    assist_triggered = (
        old_acc > 0
        and new_acc > old_acc * config.PATTERN_A_NEW_ACC_RATIO
        and only_buy >= config.PATTERN_A_NEW_ACC_MIN_CNT
    )

    if turnover > config.PATTERN_A_ROSTER_RED:
        level = LEVEL_RED
    elif turnover > config.PATTERN_A_ROSTER_YELLOW:
        level = LEVEL_YELLOW
        if assist_triggered:
            level = LEVEL_RED
    elif assist_triggered:
        level = LEVEL_YELLOW
    else:
        return LEVEL_NONE, {}

    detail["level"] = level
    detail["assist_triggered"] = assist_triggered
    return level, detail


# ============================================================
# 模式 B — 新鲸下场
# ============================================================

def detect_pattern_b(diff: SnapshotDiff) -> tuple[Optional[str], dict]:
    if diff.new_acc_count == 0:
        return LEVEL_NONE, {}

    avg_score   = diff.new_acc_avg_score
    hold_sum    = diff.new_acc_hold_sum
    only_buy_r  = diff.new_acc_only_buy / max(diff.new_acc_count, 1)

    detail = {
        "new_acc_count": diff.new_acc_count,
        "new_acc_avg_score": avg_score,
        "new_acc_only_buy_pct": round(only_buy_r * 100, 1),
        "new_acc_hold_sum_pct": round(hold_sum, 3),
    }

    if (
        avg_score >= config.PATTERN_B_ACC_SCORE_MIN
        and only_buy_r >= config.PATTERN_B_ONLY_BUY_RATIO
        and hold_sum >= config.PATTERN_B_HOLD_PCT_YELLOW
    ):
        level = LEVEL_RED if hold_sum >= config.PATTERN_B_HOLD_PCT_RED else LEVEL_YELLOW
        detail["level"] = level
        return level, detail

    return LEVEL_NONE, {}


# ============================================================
# 模式 C — 爆发前静默
# ============================================================

def detect_pattern_c(diff: SnapshotDiff) -> tuple[Optional[str], dict, int]:
    hold_new = diff.acc_hold_new
    med      = diff.historical_acc_hold_median
    turnover = diff.roster_turnover_pct
    delta_c  = diff.delta_acc_count
    only_buy = diff.latest_only_buy_pct

    cond_1 = (med > 0 and hold_new >= med * config.PATTERN_C_HOLD_RATIO_VS_MEDIAN)
    cond_2 = (turnover < config.PATTERN_C_TURNOVER_MAX)
    cond_3 = (delta_c >= 0)
    cond_4 = (only_buy >= config.PATTERN_C_ONLY_BUY_MIN)

    met = sum([cond_1, cond_2, cond_3, cond_4])

    detail = {
        "conditions_met": met,
        "acc_hold_new_pct": round(hold_new, 3),
        "historical_median_pct": round(med, 3),
        "turnover_pct": round(turnover * 100, 1),
        "only_buy_pct": round(only_buy * 100, 1),
    }

    if met >= config.PATTERN_C_RED_CONDITIONS:
        return LEVEL_RED, detail, met
    elif met >= config.PATTERN_C_YELLOW_CONDITIONS:
        return LEVEL_YELLOW, detail, met

    return LEVEL_NONE, {}, met


# ============================================================
# 模式 E — 钻石绞杀区 (V8.2)
# ============================================================

def detect_pattern_e(diff: SnapshotDiff) -> tuple[Optional[str], dict]:
    """
    双90%阈值判定：institutional_hold_v8 >= 90% AND dex_verified_pct >= 90%
    数据均为 100 进制 (90.0 = 90%)
    """
    th_inst = float(os.getenv("DIAMOND_INST_THRESHOLD", "90.0"))
    th_dex  = float(os.getenv("DIAMOND_DEX_THRESHOLD", "90.0"))

    if diff.institutional_hold_v8 >= th_inst and diff.dex_verified_pct >= th_dex:
        return LEVEL_DIAMOND, {"desc": "极低流通+真金吸筹"}
    return LEVEL_NONE, {}


# ============================================================
# 综合信号评级
# ============================================================

def _composite_level(
    a: Optional[str],
    b: Optional[str],
    c: Optional[str],
    e: Optional[str],
) -> tuple[Optional[str], list[str]]:
    """合并四模式结果。DIAMOND 优先级最高。"""

    # E(DIAMOND) 独立通道，直接返回
    if e == LEVEL_DIAMOND:
        return LEVEL_DIAMOND, ["E(DIAMOND)"]

    triggered = []
    if a:
        triggered.append(f"A({a})")
    if b:
        triggered.append(f"B({b})")
    if c:
        triggered.append(f"C({c})")

    if not triggered:
        return LEVEL_NONE, []

    if a == LEVEL_RED and b and c:
        return LEVEL_EXTREME, triggered
    if a == LEVEL_RED and b:
        return LEVEL_CRITICAL, triggered
    if LEVEL_RED in (a, b, c):
        return LEVEL_RED, triggered

    return LEVEL_YELLOW, triggered


# ============================================================
# 单代币检测
# ============================================================

def detect(ts: TokenTimeSeries) -> Optional[PatternResult]:
    diff = ts.latest_diff
    if diff is None:
        return None

    a_level, a_detail               = detect_pattern_a(diff)
    b_level, b_detail               = detect_pattern_b(diff)
    c_level, c_detail, c_conditions = detect_pattern_c(diff)
    e_level, e_detail               = detect_pattern_e(diff)

    comp_level, triggered = _composite_level(a_level, b_level, c_level, e_level)

    return PatternResult(
        chain=ts.chain,
        token_address=ts.token_address,
        token_symbol=ts.token_symbol,
        snap_count=ts.snap_count,
        latest_snapshot=ts.latest_snapshot,
        pattern_a_level=a_level,
        pattern_a_detail=a_detail,
        pattern_b_level=b_level,
        pattern_b_detail=b_detail,
        pattern_c_level=c_level,
        pattern_c_detail=c_detail,
        pattern_c_conditions_met=c_conditions,
        composite_level=comp_level,
        triggered_patterns=triggered,
        roster_turnover_pct=diff.roster_turnover_pct,
        hours_gap=diff.hours_gap,
        gap_warning=diff.gap_warning,
        new_acc_count=diff.new_acc_count,
        new_acc_only_buy=diff.new_acc_only_buy,
        latest_only_buy_pct=diff.latest_only_buy_pct,
        acc_hold_new=diff.acc_hold_new,
        delta_acc_hold=diff.delta_acc_hold,
        delta_acc_count=diff.delta_acc_count,
        acc_count_new=diff.acc_count_new,
        institutional_hold_v8=diff.institutional_hold_v8,
        hidden_whale_count=diff.hidden_whale_count,
        dex_verified_pct=diff.dex_verified_pct,
    )


# ============================================================
# 批量检测
# ============================================================

def scan_all(
    time_series_list: list[TokenTimeSeries],
) -> list[PatternResult]:
    level_order = {
        LEVEL_DIAMOND:  0,
        LEVEL_EXTREME:  1,
        LEVEL_CRITICAL: 2,
        LEVEL_RED:      3,
        LEVEL_YELLOW:   4,
        LEVEL_NONE:     9,
    }

    results = []
    for ts in time_series_list:
        r = detect(ts)
        if r is not None:
            results.append(r)

    results.sort(key=lambda r: level_order.get(r.composite_level, 9))
    return results


def get_signaled(results: list[PatternResult]) -> list[PatternResult]:
    return [r for r in results if r.has_signal]


def get_red_and_above(results: list[PatternResult]) -> list[PatternResult]:
    return [r for r in results if r.is_red_or_above]
