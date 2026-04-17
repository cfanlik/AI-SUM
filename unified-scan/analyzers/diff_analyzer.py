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
A2: 地址聚合 + A3: 新鲸下场
来源: master-scan pattern_detector + time_series_aligner
基于相邻快照 diff 计算换手率和新进吸筹者画像。
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
import config


@dataclass
class SnapshotDiff:
    t_old: str
    t_new: str
    hours_gap: float
    gap_warning: bool

    # 名册换手
    roster_turnover_pct: float = 0.0
    new_acc_count: int = 0
    new_acc_only_buy: int = 0
    new_acc_avg_score: float = 0.0
    new_acc_hold_sum: float = 0.0

    # 吸筹地址统计
    acc_count_old: int = 0
    acc_count_new: int = 0
    delta_acc_count: int = 0
    acc_hold_old: float = 0.0
    acc_hold_new: float = 0.0
    delta_acc_hold: float = 0.0

    # 只买不卖比例
    latest_only_buy_pct: float = 0.0


def compute_diff(old_holders: list[dict], new_holders: list[dict],
                 t_old: str, t_new: str) -> SnapshotDiff:
    """计算两个快照之间的 diff。"""
    try:
        dt_old = datetime.strptime(t_old[:19], "%Y-%m-%d %H:%M:%S")
        dt_new = datetime.strptime(t_new[:19], "%Y-%m-%d %H:%M:%S")
        hours_gap = (dt_new - dt_old).total_seconds() / 3600
    except Exception:
        hours_gap = 10.0

    diff = SnapshotDiff(
        t_old=t_old, t_new=t_new,
        hours_gap=hours_gap,
        gap_warning=hours_gap > config.MAX_HOURS_GAP,
    )

    old_addrs = {h["wallet_address"] for h in old_holders}
    new_addrs = {h["wallet_address"] for h in new_holders}

    # 名册换手率
    if old_addrs:
        diff.roster_turnover_pct = len(new_addrs - old_addrs) / len(old_addrs)

    # 旧快照吸筹统计
    old_acc_addrs = set()
    for h in old_holders:
        if h.get("is_accumulating"):
            old_acc_addrs.add(h["wallet_address"])
            diff.acc_hold_old += h.get("hold_percentage", 0) or 0
    diff.acc_count_old = len(old_acc_addrs)

    # 新快照吸筹统计
    new_acc_scores = []
    only_buy_count = 0
    total_acc_new = 0

    for h in new_holders:
        if not h.get("is_accumulating"):
            continue
        total_acc_new += 1
        diff.acc_hold_new += h.get("hold_percentage", 0) or 0

        sell = h.get("sell_amt_usd") or 0
        buy = h.get("buy_amt_usd") or 0
        if sell == 0 and buy > 0:
            only_buy_count += 1

        addr = h["wallet_address"]
        if addr not in old_acc_addrs:
            diff.new_acc_count += 1
            new_acc_scores.append(h.get("acc_score", 0) or 0)
            diff.new_acc_hold_sum += h.get("hold_percentage", 0) or 0
            if sell == 0 and buy > 0:
                diff.new_acc_only_buy += 1

    diff.acc_count_new = total_acc_new
    diff.delta_acc_count = total_acc_new - diff.acc_count_old
    diff.delta_acc_hold = diff.acc_hold_new - diff.acc_hold_old
    diff.latest_only_buy_pct = only_buy_count / max(total_acc_new, 1)

    if new_acc_scores:
        diff.new_acc_avg_score = sum(new_acc_scores) / len(new_acc_scores)

    return diff


def check_a2(diff: SnapshotDiff) -> dict:
    """A2: 地址聚合检测。"""
    turnover = diff.roster_turnover_pct
    new_acc = diff.new_acc_count
    old_acc = diff.acc_count_old
    only_buy = diff.new_acc_only_buy

    assist = (
        old_acc > 0
        and new_acc > old_acc * config.A2_NEW_ACC_RATIO
        and only_buy >= config.A2_NEW_ACC_MIN_CNT
    )

    if turnover > config.A2_ROSTER_RED:
        level = "RED"
    elif turnover > config.A2_ROSTER_YELLOW:
        level = "RED" if assist else "YELLOW"
    elif assist:
        level = "YELLOW"
    else:
        return {"triggered": False, "level": None}

    return {
        "triggered": True,
        "level": level,
        "turnover_pct": round(turnover * 100, 1),
        "new_acc": new_acc,
        "assist": assist,
    }


def check_a3(diff: SnapshotDiff) -> dict:
    """A3: 新鲸下场检测。"""
    if diff.new_acc_count == 0:
        return {"triggered": False, "level": None}

    avg_score = diff.new_acc_avg_score
    hold_sum = diff.new_acc_hold_sum
    only_buy_r = diff.new_acc_only_buy / max(diff.new_acc_count, 1)

    if (avg_score >= config.A3_ACC_SCORE_MIN
            and only_buy_r >= config.A3_ONLY_BUY_RATIO
            and hold_sum >= config.A3_HOLD_PCT_MIN):
        level = "RED" if hold_sum >= 2.0 else "YELLOW"
        return {
            "triggered": True,
            "level": level,
            "avg_score": round(avg_score, 1),
            "only_buy_pct": round(only_buy_r * 100, 1),
            "hold_sum": round(hold_sum, 3),
            "count": diff.new_acc_count,
        }

    return {"triggered": False, "level": None}
