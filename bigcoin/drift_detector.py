"""
whale-scan — 持仓漂移检测器
S2: 首末快照 Top10 持仓比例变化 vs 买入金额变化
核心逻辑：持仓比例上升 + 买入金额不变 = 被动集中 = 庄控信号
"""
from __future__ import annotations
from dataclasses import dataclass, field
import config


@dataclass
class DriftResult:
    """Top10 持仓漂移检测结果"""
    # Top1/2 漂移
    whale1_hold_first: float = 0.0
    whale1_hold_last: float = 0.0
    whale1_hold_delta: float = 0.0
    whale1_buy_unchanged: bool = False

    whale2_hold_first: float = 0.0
    whale2_hold_last: float = 0.0
    whale2_hold_delta: float = 0.0
    whale2_buy_unchanged: bool = False

    # Top10 中被动漂移地址数
    passive_drift_count: int = 0
    passive_drift_details: list[dict] = field(default_factory=list)

    # 吸筹地址变化
    acc_cnt_first: int = 0
    acc_cnt_last: int = 0
    acc_pct_first: float = 0.0
    acc_pct_last: float = 0.0

    # 快照信息
    first_snapshot: str = ""
    last_snapshot: str = ""
    snap_count: int = 0


def detect_drift(
    first_holders: list[dict],
    last_holders: list[dict],
    acc_stats: list[dict],
    first_snap: str,
    last_snap: str,
    snap_count: int,
) -> DriftResult:
    """
    对比首末快照 Top10 持仓变化。
    first_holders / last_holders: 按 hold_percentage DESC 排序的持仓列表
    acc_stats: [{snapshot_time, acc_cnt, acc_pct}, ...]
    """
    dr = DriftResult(
        first_snapshot=first_snap,
        last_snapshot=last_snap,
        snap_count=snap_count,
    )

    if not first_holders or not last_holders:
        return dr

    # ── 构建首快照地址→持仓映射 ──
    first_map = {}
    for h in first_holders[:20]:
        addr = h.get("wallet_address", "")
        first_map[addr] = {
            "hold": h.get("hold_percentage", 0) or 0,
            "buy": h.get("buy_amt_usd", 0) or 0,
        }

    # ── 遍历末快照 Top10，检测漂移 ──
    for i, h in enumerate(last_holders[:10]):
        addr = h.get("wallet_address", "")
        hold_now = h.get("hold_percentage", 0) or 0
        buy_now = h.get("buy_amt_usd", 0) or 0

        prev = first_map.get(addr)
        if prev is None:
            continue

        hold_prev = prev["hold"]
        buy_prev = prev["buy"]
        delta = hold_now - hold_prev
        buy_same = abs(buy_now - buy_prev) < 1.0  # 允许 $1 浮动

        if i == 0:
            dr.whale1_hold_first = hold_prev
            dr.whale1_hold_last = hold_now
            dr.whale1_hold_delta = delta
            dr.whale1_buy_unchanged = buy_same and delta > 0

        if i == 1:
            dr.whale2_hold_first = hold_prev
            dr.whale2_hold_last = hold_now
            dr.whale2_hold_delta = delta
            dr.whale2_buy_unchanged = buy_same and delta > 0

        # Top10 被动漂移统计
        if delta > 1.0 and buy_same:
            dr.passive_drift_count += 1
            dr.passive_drift_details.append({
                "rank": i + 1,
                "address": addr[:16] + "...",
                "hold_first": round(hold_prev, 2),
                "hold_last": round(hold_now, 2),
                "delta": round(delta, 2),
            })

    # ── 吸筹地址变化 ──
    if acc_stats:
        dr.acc_cnt_first = acc_stats[0].get("acc_cnt", 0) or 0
        dr.acc_cnt_last = acc_stats[-1].get("acc_cnt", 0) or 0
        dr.acc_pct_first = acc_stats[0].get("acc_pct", 0) or 0
        dr.acc_pct_last = acc_stats[-1].get("acc_pct", 0) or 0

    return dr
