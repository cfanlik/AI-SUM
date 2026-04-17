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
# S4(V/L=x)        — 换手效率 → V/L>10 极端换手（标记不计分）
# G2(LP=$x)        — LP 流动性门控 → <$30K降级, <$10K否决
# G3(死池)         — V/L<0.01 + Vol<$100 → 否决ACC信号
# ──────────────────────────────────────────────────────────

"""
A4: CEX 流出（吸筹信号）+ D1: CEX 流入（出货信号）
来源: opus-scan time_series_builder + verdict_engine
基于全快照 CEX 占比时序的线性回归斜率。
"""
from __future__ import annotations
import config


def _linear_slope(values: list[float]) -> float:
    """OLS 线性斜率（无 numpy 依赖）"""
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den if den != 0 else 0.0


def analyze_cex_flow(stats_series: list[dict]) -> dict:
    """
    从全快照统计序列计算 CEX 流向。
    返回: {cex_earliest, cex_latest, cex_delta_pct, cex_slope, a4, d1}
    """
    if len(stats_series) < 2:
        return {
            "cex_earliest": 0, "cex_latest": 0, "cex_delta_pct": 0,
            "cex_slope": 0,
            "a4": {"triggered": False}, "d1": {"triggered": False},
        }

    earliest = stats_series[0]
    latest = stats_series[-1]
    cex_earliest = earliest.get("cex_hold") or 0
    cex_latest = latest.get("cex_hold") or 0

    # 绝对百分点差值（与opus-scan一致：如 31%→87% = +56个百分点）
    cex_delta_pct = round(cex_latest - cex_earliest, 1)

    # 线性斜率（最近 min(8, total) 个点）
    window = stats_series[-min(8, len(stats_series)):]
    cex_values = [float(s.get("cex_hold") or 0) for s in window]
    cex_slope = round(_linear_slope(cex_values), 3)

    # A4: CEX 流出（吸筹信号）
    a4_triggered = (cex_delta_pct < config.CEX_OUTFLOW_DELTA
                    and cex_slope < config.CEX_OUTFLOW_SLOPE)
    a4 = {
        "triggered": a4_triggered,
        "detail": f"CEX {cex_earliest:.1f}%→{cex_latest:.1f}% (Δ{cex_delta_pct:+.1f}%, 斜率{cex_slope:+.3f})",
    }

    # D1: CEX 流入（出货信号）
    d1_triggered = (cex_delta_pct > config.CEX_INFLOW_DELTA
                    and cex_slope > config.CEX_INFLOW_SLOPE)
    d1 = {
        "triggered": d1_triggered,
        "detail": f"CEX {cex_earliest:.1f}%→{cex_latest:.1f}% (Δ{cex_delta_pct:+.1f}%, 斜率{cex_slope:+.3f})",
    }

    return {
        "cex_earliest": round(cex_earliest, 2),
        "cex_latest": round(cex_latest, 2),
        "cex_delta_pct": cex_delta_pct,
        "cex_slope": cex_slope,
        "a4": a4,
        "d1": d1,
    }
