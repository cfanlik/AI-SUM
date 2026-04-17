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
D2: 出货者画像
来源: opus-scan holder_profiler
Top N holders → 出货者 / 假鲸鱼 / 48h派发者 分类
"""
from __future__ import annotations
import config


def profile_holders(latest_holders: list[dict]) -> dict:
    """
    从最新快照 Top N 持仓者中识别出货行为。
    返回: {triggered, seller_count, seller_hold_pct, fake_whale_count,
           fake_whale_hold_pct, dist_48h_count, details}
    """
    sellers = []
    seller_hold = 0.0
    fake_whales = []
    fake_whale_hold = 0.0
    distributors_48h = []

    for h in latest_holders:
        hold_pct = h.get("hold_percentage") or 0
        buy = h.get("buy_cnt") or 0
        sell = h.get("sell_cnt") or 0
        dex_r = h.get("dex_ratio")
        if dex_r is None:
            dex_r = -1
        h48_in = h.get("recent_48h_in") or 0
        h48_out = h.get("recent_48h_out") or 0
        is_infra = (h.get("is_cex") or h.get("is_contract")
                    or h.get("is_supernode") or h.get("is_dex"))

        addr_short = (h.get("wallet_address") or "?")[:12] + "..."

        # 出货者: 卖/买比≥3
        if sell > 0 and buy > 0 and sell / max(buy, 1) >= config.D2_SELL_BUY_RATIO:
            sellers.append({"addr": addr_short, "hold": hold_pct,
                            "ratio": round(sell / max(buy, 1), 1)})
            seller_hold += hold_pct

        # 假鲸鱼: 非基础设施 + 持仓≥2% + DEX<0.05 + 买入≤1
        if (not is_infra and hold_pct >= 2.0
                and 0 <= dex_r < 0.05 and buy <= 1):
            fake_whales.append({"addr": addr_short, "hold": hold_pct, "dex": dex_r})
            fake_whale_hold += hold_pct

        # 48h 派发者: 有出无进 + 持仓≥0.5%
        if h48_out > 0 and h48_in == 0 and hold_pct >= 0.5:
            distributors_48h.append({"addr": addr_short, "hold": hold_pct,
                                     "h48_out": h48_out})

    seller_count = len(sellers)
    fake_count = len(fake_whales)
    dist_count = len(distributors_48h)

    # 触发条件: 任一子信号满足
    has_major_seller = any(s["hold"] >= config.D2_SELLER_HOLD_MIN for s in sellers)
    has_fake_whales = fake_count >= config.D2_FAKE_WHALE_MIN
    has_48h_dist = dist_count >= config.D2_DIST_48H_MIN

    triggered = has_major_seller or has_fake_whales or has_48h_dist

    return {
        "triggered": triggered,
        "seller_count": seller_count,
        "seller_hold_pct": round(seller_hold, 2),
        "fake_whale_count": fake_count,
        "fake_whale_hold_pct": round(fake_whale_hold, 2),
        "dist_48h_count": dist_count,
        "has_major_seller": has_major_seller,
        "has_fake_whales": has_fake_whales,
        "has_48h_dist": has_48h_dist,
    }
