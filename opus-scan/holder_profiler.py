"""
opus-scan — 大户画像
Top N holders → 出货者 / 真金吸筹 / 假鲸鱼 分类
"""
from __future__ import annotations
from dataclasses import dataclass, field
import config


@dataclass
class HolderProfile:
    latest_snapshot: str
    earliest_snapshot: str
    top_n: int

    sellers: list[dict] = field(default_factory=list)
    real_accumulators: list[dict] = field(default_factory=list)
    fake_whales: list[dict] = field(default_factory=list)
    distributors_48h: list[dict] = field(default_factory=list)

    seller_count: int = 0
    seller_hold_pct: float = 0.0
    acc_count: int = 0
    acc_hold_pct: float = 0.0
    fake_whale_count: int = 0
    fake_whale_hold_pct: float = 0.0
    distribution_48h_count: int = 0
    dex_verified_pct: float = 0.0
    strong_buyer_count: int = 0
    net_inflow_all_positive: bool = False


def build_profile(
    latest_holders: list[dict],
    earliest_holders: list[dict],
    latest_snap: str,
    earliest_snap: str,
) -> HolderProfile:
    profile = HolderProfile(
        latest_snapshot=latest_snap,
        earliest_snapshot=earliest_snap,
        top_n=len(latest_holders),
    )

    acc_with_dex = 0
    acc_total = 0
    acc_all_inflow_pos = True
    strong_buyers = 0

    for h in latest_holders:
        hold_pct = h.get("hold_percentage") or 0
        buy = h.get("buy_cnt") or 0
        sell = h.get("sell_cnt") or 0
        is_acc = h.get("is_accumulating") or 0
        dex = h.get("dex_ratio")
        dex_r = dex if dex is not None else -1
        net = h.get("net_inflow") or 0
        h48_in = h.get("recent_48h_in") or 0
        h48_out = h.get("recent_48h_out") or 0
        is_infra = (h.get("is_cex") or h.get("is_contract") or
                    h.get("is_supernode") or h.get("is_dex"))

        addr_short = (h.get("wallet_address") or "?")[:12] + "..."

        # ── 出货者 ── (使用金额比与抛售量综合判定)
        buy_usd = h.get("buy_amt_usd") or 0
        sell_usd = h.get("sell_amt_usd") or 0
        is_seller = (sell_usd > 0 and buy_usd == 0) or \
                    (sell_usd > 0 and buy_usd > 0 and sell_usd / max(buy_usd, 1) >= 0.5) or \
                    (sell > 0 and buy == 0) or \
                    (h48_out > 0 and h48_in == 0 and (hold_pct >= 0.1 or h48_out >= 50000))
        if is_seller:
            entry = {
                "addr": addr_short, "hold": hold_pct,
                "buy": buy_usd, "sell": sell_usd,
                "ratio": round(sell_usd / max(buy_usd, 1), 1) if buy_usd > 0 else 999.0,
                "h48_out": h48_out,
            }
            profile.sellers.append(entry)
            profile.seller_hold_pct += hold_pct

        # ── 假鲸鱼 ──
        if (not is_infra and hold_pct >= 2.0
                and (dex_r < 0.05 or dex_r == -1)
                and buy <= 1):
            entry = {
                "addr": addr_short, "hold": hold_pct,
                "buy": buy, "dex_ratio": dex_r,
            }
            profile.fake_whales.append(entry)
            profile.fake_whale_hold_pct += hold_pct

        # ── 48h 派发者 ── (放宽门槛，捕获中大户分散出货)
        if h48_out > 0 and h48_in == 0 and (hold_pct >= 0.1 or h48_out >= 50000):
            profile.distributors_48h.append({
                "addr": addr_short, "hold": hold_pct,
                "h48_out": h48_out,
            })

        # ── 真金吸筹者 ──
        if is_acc:
            acc_total += 1
            profile.acc_hold_pct += hold_pct
            if dex_r > 0.5:
                acc_with_dex += 1
            if net <= 0:
                acc_all_inflow_pos = False
            if buy > sell * 3 and buy >= 10 and not (h48_out > 0 and h48_in == 0):
                strong_buyers += 1
            profile.real_accumulators.append({
                "addr": addr_short, "hold": hold_pct,
                "score": h.get("acc_score") or 0,
                "buy": buy, "sell": sell,
                "dex_ratio": dex_r,
            })

    profile.seller_count = len(profile.sellers)
    profile.seller_hold_pct = round(profile.seller_hold_pct, 2)
    profile.acc_count = acc_total
    profile.acc_hold_pct = round(profile.acc_hold_pct, 2)
    profile.fake_whale_count = len(profile.fake_whales)
    profile.fake_whale_hold_pct = round(profile.fake_whale_hold_pct, 2)
    profile.distribution_48h_count = len(profile.distributors_48h)
    profile.dex_verified_pct = round(
        acc_with_dex / max(acc_total, 1) * 100, 1
    )
    profile.strong_buyer_count = strong_buyers
    profile.net_inflow_all_positive = acc_all_inflow_pos and acc_total > 0

    return profile
