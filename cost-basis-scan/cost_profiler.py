"""
cost-basis-scan 成本画像分析器
四成本带分层 + VWAP + 成本 CV + 成本倒挂
"""
from __future__ import annotations
import math
import logging
from dataclasses import dataclass, field
from db_loader import CostHolder
import config

logger = logging.getLogger(__name__)


@dataclass
class CostZone:
    """单个成本带"""
    name: str
    holders: list[CostHolder] = field(default_factory=list)
    hold_pct_sum: float = 0.0       # 持仓占比之和
    holder_count: int = 0

    @property
    def avg_cost(self) -> float:
        if not self.holders:
            return 0
        return sum(h.gmgn_avg_price for h in self.holders) / len(self.holders)


@dataclass
class CostProfile:
    """代币成本画像"""
    chain: str
    token_address: str
    gecko_price: float
    cost_holders_count: int

    # VWAP
    vwap: float = 0.0

    # 四成本带
    deep_underwater: CostZone = field(default_factory=lambda: CostZone(name="deep_underwater"))
    shallow_underwater: CostZone = field(default_factory=lambda: CostZone(name="shallow_underwater"))
    profit_zone: CostZone = field(default_factory=lambda: CostZone(name="profit_zone"))
    windfall_zone: CostZone = field(default_factory=lambda: CostZone(name="windfall_zone"))

    # 成本 CV
    cost_cv: float = 0.0

    # 成本倒挂 (Top5 VWAP vs Rank50-300 VWAP)
    top5_vwap: float = 0.0
    tail_vwap: float = 0.0
    cost_inverted: bool = False

    # C1: 水下逆势加仓
    underwater_adding_count: int = 0   # 水下区加仓地址数
    underwater_adding_total: int = 0   # 水下区总地址数

    # C4: 暴利出逃
    windfall_selling_count: int = 0    # 暴利区有卖出的地址数
    windfall_selling_details: list = field(default_factory=list)

    # C5: 高位派发
    new_at_market_count: int = 0       # 新地址成本≈现价的数量
    old_whale_declining: bool = False   # 老鲸鱼持仓是否下降

    # C8: 暴利区 48h 净流出
    windfall_48h_net_out: float = 0.0  # 暴利区地址的 48h 净流出总和
    windfall_total_hold: float = 0.0   # 暴利区地址总持仓

    # 快照间距
    snapshot_gap_hours: float = 0.0    # 当前与上一有成本快照的时间差(小时)


def build_profile(
    holders: list[CostHolder],
    prev_holders: list[CostHolder],
    gecko_price: float,
    chain: str,
    token_address: str,
) -> CostProfile:
    """构建代币成本画像"""
    profile = CostProfile(
        chain=chain,
        token_address=token_address,
        gecko_price=gecko_price,
        cost_holders_count=len(holders),
    )

    if not holders or gecko_price <= 0:
        return profile

    # ── 快照间距计算 ──
    if holders and prev_holders:
        try:
            from datetime import datetime
            curr_time = datetime.strptime(holders[0].snapshot_time, "%Y-%m-%d %H:%M:%S")
            prev_time = datetime.strptime(prev_holders[0].snapshot_time, "%Y-%m-%d %H:%M:%S")
            profile.snapshot_gap_hours = (curr_time - prev_time).total_seconds() / 3600
        except (ValueError, IndexError):
            profile.snapshot_gap_hours = 0

    # ── 构建上一快照的 wallet → holder 映射 ──
    prev_map: dict[str, CostHolder] = {}
    for h in prev_holders:
        prev_map[h.wallet_address] = h

    # ── VWAP 计算 ──
    total_buy_amount = sum(h.gmgn_buy_amount for h in holders if h.gmgn_buy_amount > 0)
    if total_buy_amount > 0:
        profile.vwap = sum(
            h.gmgn_buy_amount * h.gmgn_avg_price
            for h in holders if h.gmgn_buy_amount > 0
        ) / total_buy_amount

    # ── 四成本带分层 ──
    for h in holders:
        ratio = h.gmgn_avg_price / gecko_price

        if ratio > config.ZONE_DEEP_UNDERWATER:
            zone = profile.deep_underwater
        elif ratio > config.ZONE_SHALLOW_UNDERWATER:
            zone = profile.shallow_underwater
        elif ratio > config.ZONE_PROFIT:
            zone = profile.profit_zone
        else:
            zone = profile.windfall_zone

        zone.holders.append(h)
        zone.hold_pct_sum += h.hold_percentage
        zone.holder_count += 1

    # ── 成本 CV ──
    prices = [h.gmgn_avg_price for h in holders]
    if len(prices) >= 2:
        mean_price = sum(prices) / len(prices)
        if mean_price > 0:
            variance = sum((p - mean_price) ** 2 for p in prices) / len(prices)
            std_dev = math.sqrt(variance)
            profile.cost_cv = std_dev / mean_price

    # ── Top5 vs Tail VWAP (成本倒挂) ──
    top5 = [h for h in holders if h.rank <= 5]
    tail = [h for h in holders if h.rank >= 50]

    top5_buy_total = sum(h.gmgn_buy_amount for h in top5 if h.gmgn_buy_amount > 0)
    if top5_buy_total > 0:
        profile.top5_vwap = sum(
            h.gmgn_buy_amount * h.gmgn_avg_price
            for h in top5 if h.gmgn_buy_amount > 0
        ) / top5_buy_total

    tail_buy_total = sum(h.gmgn_buy_amount for h in tail if h.gmgn_buy_amount > 0)
    if tail_buy_total > 0:
        profile.tail_vwap = sum(
            h.gmgn_buy_amount * h.gmgn_avg_price
            for h in tail if h.gmgn_buy_amount > 0
        ) / tail_buy_total

    if profile.top5_vwap > 0 and profile.tail_vwap > 0:
        profile.cost_inverted = profile.top5_vwap > profile.tail_vwap * 1.2

    # ── C1: 水下逆势加仓 ──
    underwater_holders = profile.deep_underwater.holders + profile.shallow_underwater.holders
    profile.underwater_adding_total = len(underwater_holders)
    for h in underwater_holders:
        prev = prev_map.get(h.wallet_address)
        if prev and h.buy_cnt > prev.buy_cnt and h.hold_amount >= prev.hold_amount * 0.95:
            profile.underwater_adding_count += 1

    # ── C4: 暴利出逃 ──
    for h in profile.windfall_zone.holders:
        prev = prev_map.get(h.wallet_address)
        if h.sell_cnt > 0:
            if prev and h.hold_amount < prev.hold_amount * 0.95:
                profile.windfall_selling_count += 1
                profile.windfall_selling_details.append({
                    "wallet": h.wallet_address[:10],
                    "cost": h.gmgn_avg_price,
                    "hold_pct": h.hold_percentage,
                    "sell_cnt": h.sell_cnt,
                })
            elif not prev:
                # 无历史对比，仅看 sell_cnt > 0
                if h.sell_cnt > 0 and h.recent_48h_out > 0:
                    profile.windfall_selling_count += 1
                    profile.windfall_selling_details.append({
                        "wallet": h.wallet_address[:10],
                        "cost": h.gmgn_avg_price,
                        "hold_pct": h.hold_percentage,
                        "sell_cnt": h.sell_cnt,
                    })

    # ── C5: 高位派发 ──
    new_at_market = []
    for h in holders:
        cost_ratio = h.gmgn_avg_price / gecko_price
        if (config.C5_NEW_COST_LOW <= cost_ratio <= config.C5_NEW_COST_HIGH
                and h.is_new_buyer == 1):
            new_at_market.append(h)
    profile.new_at_market_count = len(new_at_market)

    # 检查老鲸鱼持仓是否下降
    if prev_holders:
        old_whales_current = [h for h in holders if h.rank <= 10 and not h.is_new_buyer]
        old_whales_prev = [prev_map[h.wallet_address] for h in old_whales_current
                           if h.wallet_address in prev_map]
        if old_whales_prev:
            current_hold = sum(h.hold_percentage for h in old_whales_current
                               if h.wallet_address in prev_map)
            prev_hold = sum(h.hold_percentage for h in old_whales_prev)
            profile.old_whale_declining = current_hold < prev_hold * 0.9

    # ── C8: 暴利区 48h 净流出 ──
    for h in profile.windfall_zone.holders:
        net_out = h.recent_48h_out - h.recent_48h_in
        if net_out > 0:
            profile.windfall_48h_net_out += net_out
        profile.windfall_total_hold += h.hold_amount

    return profile
