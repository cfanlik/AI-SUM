"""
opus-scan — 时序分析
全部快照 → 趋势向量（斜率、增长率、阶段判定）
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime


@dataclass
class TimeSeriesResult:
    chain: str
    token_address: str
    symbol: str
    snap_count: int
    days_span: float

    acc_cnt_earliest: int
    acc_cnt_latest: int
    acc_cnt_slope: float

    acc_hold_earliest: float
    acc_hold_latest: float
    acc_hold_growth_pct: float

    cex_hold_earliest: float
    cex_hold_latest: float
    cex_delta_pct: float

    supernode_delta: int
    hidden_whale_latest: int
    avg_score_latest: float

    # 阶段
    phase: str  # early_acc / plateau / accelerating / topping / distributing / unknown

    # 派生斜率（有默认值，必须排在无默认值字段之后）
    cex_hold_slope: float = 0.0   # CEX 占比线性斜率（正=流入，负=流出）


def _linear_slope(values: list[float]) -> float:
    """手写 OLS 避免 numpy 依赖"""
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den if den != 0 else 0.0


def _safe_pct_change(old: float, new: float) -> float:
    if old == 0:
        return 100.0 if new > 0 else 0.0
    return (new - old) / abs(old) * 100


def build_time_series(
    stats_series: list[dict],
    chain: str,
    token_address: str,
    symbol: str,
) -> TimeSeriesResult | None:
    if len(stats_series) < 2:
        return None

    earliest = stats_series[0]
    latest = stats_series[-1]

    # 时间跨度
    try:
        t0 = datetime.strptime(earliest["snapshot_time"][:19], "%Y-%m-%d %H:%M:%S")
        t1 = datetime.strptime(latest["snapshot_time"][:19], "%Y-%m-%d %H:%M:%S")
        days_span = max((t1 - t0).total_seconds() / 86400, 0.01)
    except Exception:
        days_span = 1.0

    # 趋势计算（用最近 min(8, total) 个点）
    window = stats_series[-min(8, len(stats_series)):]
    acc_cnts = [s["acc_cnt"] or 0 for s in window]
    acc_holds = [s["acc_hold_pct"] or 0 for s in window]
    cex_holds = [s["cex_hold"] or 0 for s in window]

    acc_cnt_slope = _linear_slope([float(x) for x in acc_cnts])
    cex_hold_slope = _linear_slope([float(x) for x in cex_holds])
    acc_hold_growth = _safe_pct_change(
        earliest["acc_hold_pct"] or 0.001,
        latest["acc_hold_pct"] or 0
    )
    cex_delta = _safe_pct_change(
        earliest["cex_hold"] or 0.001,
        latest["cex_hold"] or 0
    )

    # 阶段判定
    phase = _determine_phase(acc_cnt_slope, acc_hold_growth, cex_delta, latest)

    return TimeSeriesResult(
        chain=chain,
        token_address=token_address,
        symbol=symbol,
        snap_count=len(stats_series),
        days_span=round(days_span, 1),
        acc_cnt_earliest=earliest["acc_cnt"] or 0,
        acc_cnt_latest=latest["acc_cnt"] or 0,
        acc_cnt_slope=round(acc_cnt_slope, 3),
        acc_hold_earliest=round(earliest["acc_hold_pct"] or 0, 3),
        acc_hold_latest=round(latest["acc_hold_pct"] or 0, 3),
        acc_hold_growth_pct=round(acc_hold_growth, 1),
        cex_hold_earliest=round(earliest["cex_hold"] or 0, 3),
        cex_hold_latest=round(latest["cex_hold"] or 0, 3),
        cex_delta_pct=round(cex_delta, 1),
        cex_hold_slope=round(cex_hold_slope, 3),
        supernode_delta=(latest.get("supernode_cnt") or 0) - (earliest.get("supernode_cnt") or 0),
        hidden_whale_latest=latest.get("hidden_whale") or 0,
        avg_score_latest=round(latest.get("avg_score") or 0, 1),
        phase=phase,
    )


def _determine_phase(
    acc_slope: float, acc_growth: float, cex_delta: float, latest: dict
) -> str:
    acc_cnt = latest.get("acc_cnt") or 0
    if acc_cnt == 0:
        return "unknown"

    if acc_growth > 50 and acc_slope > 0.5:
        return "accelerating"
    if acc_growth > 20 and acc_slope > 0:
        return "early_acc"
    if abs(acc_growth) <= 10 and abs(acc_slope) < 0.3:
        return "plateau"
    if acc_slope < -0.3 and cex_delta < -15:
        return "distributing"
    if acc_growth > 30 and acc_slope < 0.1:
        return "topping"

    return "early_acc" if acc_slope > 0 else "plateau"
