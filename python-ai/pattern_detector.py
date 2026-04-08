"""
AI-SUM V5 — 行为模式识别引擎
三大核心模式：
  A — 地址聚合（Aggregation Pattern）：Top300 换手率突升 + 新吸筹地址大量涌入
  B — 新鲸下场（Fresh Whale Pattern）：新进高分地址 + 纯买入 + 持仓合计达阈值
  C — 爆发前静默（Pre-Pump Silence）：持仓控制高位 + 换手冻结 + 纯买不卖
"""
from __future__ import annotations

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
LEVEL_CRITICAL = "CRITICAL"  # 复合信号：A(RED) + B
LEVEL_EXTREME  = "EXTREME"   # 复合信号：A + B + C 全触发


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

    # 三模式各自结果
    pattern_a_level: Optional[str] = None
    pattern_a_detail: dict = field(default_factory=dict)

    pattern_b_level: Optional[str] = None
    pattern_b_detail: dict = field(default_factory=dict)

    pattern_c_level: Optional[str] = None
    pattern_c_detail: dict = field(default_factory=dict)
    pattern_c_conditions_met: int = 0

    # 综合信号
    composite_level: Optional[str] = None
    triggered_patterns: list[str] = field(default_factory=list)

    # 最新 diff 关键指标（用于报告展示）
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
    """
    触发逻辑：
      主条件：roster_turnover_pct > 阈值
      辅条件：new_acc_count > old_acc_count * RATIO 且 new_acc_only_buy >= MIN_CNT
      辅条件成立时提升一个等级（黄→红）。
    """
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

    # 辅助条件
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
            level = LEVEL_RED   # 辅助条件升级
    elif assist_triggered:
        level = LEVEL_YELLOW    # 纯辅助条件触发黄色
    else:
        return LEVEL_NONE, {}

    detail["level"] = level
    detail["assist_triggered"] = assist_triggered
    return level, detail


# ============================================================
# 模式 B — 新鲸下场
# ============================================================

def detect_pattern_b(diff: SnapshotDiff) -> tuple[Optional[str], dict]:
    """
    触发逻辑：
      ① 新增吸筹地址均分 >= PATTERN_B_ACC_SCORE_MIN
      ② 新acc中纯买入比例 >= PATTERN_B_ONLY_BUY_RATIO
      ③ 新acc持仓合计 >= PATTERN_B_HOLD_PCT_YELLOW
    """
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
    """
    触发逻辑（评分满足条件数）：
      ① latest_acc_hold >= historical_acc_hold_median * HOLD_RATIO
      ② roster_turnover_pct < TURNOVER_MAX（12h 换手极低）
      ③ delta_acc_count >= 0（吸筹人数没减少）
      ④ latest_only_buy_pct >= ONLY_BUY_MIN

    3/4 → YELLOW，4/4 → RED
    """
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
        "cond1_hold_vs_median": cond_1,
        "cond2_turnover_low": cond_2,
        "cond3_acc_not_shrink": cond_3,
        "cond4_only_buy_high": cond_4,
        "acc_hold_new_pct": round(hold_new, 3),
        "historical_median_pct": round(med, 3),
        "turnover_pct": round(turnover * 100, 1),
        "only_buy_pct": round(only_buy * 100, 1),
    }

    if met >= config.PATTERN_C_RED_CONDITIONS:
        detail["level"] = LEVEL_RED
        return LEVEL_RED, detail, met
    elif met >= config.PATTERN_C_YELLOW_CONDITIONS:
        detail["level"] = LEVEL_YELLOW
        return LEVEL_YELLOW, detail, met

    return LEVEL_NONE, {}, met


# ============================================================
# 综合信号评级
# ============================================================

def _composite_level(
    a: Optional[str],
    b: Optional[str],
    c: Optional[str],
) -> tuple[Optional[str], list[str]]:
    """
    合并三模式结果，返回 (composite_level, triggered_patterns)。
    """
    triggered = []
    if a:
        triggered.append(f"A({a})")
    if b:
        triggered.append(f"B({b})")
    if c:
        triggered.append(f"C({c})")

    if not triggered:
        return LEVEL_NONE, []

    # 极端场景
    if a == LEVEL_RED and b and c:
        return LEVEL_EXTREME, triggered

    # 关键场景
    if a == LEVEL_RED and b:
        return LEVEL_CRITICAL, triggered

    # 任一红色
    if LEVEL_RED in (a, b, c):
        return LEVEL_RED, triggered

    # 仅黄色
    return LEVEL_YELLOW, triggered


# ============================================================
# 单代币检测
# ============================================================

def detect(ts: TokenTimeSeries) -> Optional[PatternResult]:
    """
    对单个代币的最新快照 diff 运行三模式检测。
    无有效 diff 时返回 None。
    """
    diff = ts.latest_diff
    if diff is None:
        return None

    a_level, a_detail               = detect_pattern_a(diff)
    b_level, b_detail               = detect_pattern_b(diff)
    c_level, c_detail, c_conditions = detect_pattern_c(diff)

    comp_level, triggered = _composite_level(a_level, b_level, c_level)

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
    )


# ============================================================
# 批量检测
# ============================================================

def scan_all(
    time_series_list: list[TokenTimeSeries],
) -> list[PatternResult]:
    """
    对全库时序列表运行模式检测。
    返回所有有信号的 PatternResult（按 composite_level 排序）。
    """
    level_order = {
        LEVEL_EXTREME:  0,
        LEVEL_CRITICAL: 1,
        LEVEL_RED:      2,
        LEVEL_YELLOW:   3,
        LEVEL_NONE:     9,
    }

    results = []
    for ts in time_series_list:
        r = detect(ts)
        if r is not None:
            results.append(r)

    # 按信号级别排序
    results.sort(key=lambda r: level_order.get(r.composite_level, 9))
    return results


def get_signaled(results: list[PatternResult]) -> list[PatternResult]:
    """仅返回有信号（composite_level != None）的结果。"""
    return [r for r in results if r.has_signal]


def get_red_and_above(results: list[PatternResult]) -> list[PatternResult]:
    """仅返回红色及以上级别。"""
    return [r for r in results if r.is_red_or_above]
