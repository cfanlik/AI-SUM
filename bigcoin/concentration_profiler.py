"""
whale-scan — 集中度分析器
S1: 极端集中度 | S6: 合约锁仓低流通
Top2/5/10/20 分层集中度 + DEX率 + 地址类型判定
"""
from __future__ import annotations
from dataclasses import dataclass, field
import config


@dataclass
class ConcentrationProfile:
    """集中度分析结果"""
    # Top-N 持仓占比
    top2_hold: float = 0.0
    top5_hold: float = 0.0
    top10_hold: float = 0.0
    top20_hold: float = 0.0
    top300_hold: float = 0.0

    # Top2 DEX 率
    top2_avg_dex: float = -1.0  # -1 = 无数据

    # Top2 类型
    top2_all_wallet: bool = False  # True = 均为普通钱包

    # 地址分类统计 (Top20)
    wallet_pct: float = 0.0
    contract_pct: float = 0.0
    cex_pct: float = 0.0
    dex_pct: float = 0.0

    # Top20 详情
    top20_details: list[dict] = field(default_factory=list)

    # 95% 覆盖
    cov95_count: int = 0
    cov95_addresses: list[dict] = field(default_factory=list)


def build_concentration(holders: list[dict]) -> ConcentrationProfile:
    """从最新快照的 Top300 持仓数据构建集中度画像"""
    p = ConcentrationProfile()

    if not holders:
        return p

    # ── Top-N 持仓 ──
    for i, h in enumerate(holders):
        pct = h.get("hold_percentage", 0) or 0
        if i < 2:
            p.top2_hold += pct
        if i < 5:
            p.top5_hold += pct
        if i < 10:
            p.top10_hold += pct
        if i < 20:
            p.top20_hold += pct
        p.top300_hold += pct

    # ── Top2 DEX 率 ──
    dex_vals = []
    all_wallet = True
    for h in holders[:2]:
        dr = h.get("dex_ratio")
        if dr is not None:
            dex_vals.append(dr)
        if h.get("is_cex") or h.get("is_contract") or h.get("is_dex"):
            all_wallet = False
    p.top2_avg_dex = sum(dex_vals) / len(dex_vals) if dex_vals else -1
    p.top2_all_wallet = all_wallet

    # ── 地址分类统计 (Top20) ──
    for h in holders[:20]:
        pct = h.get("hold_percentage", 0) or 0
        if h.get("is_contract"):
            p.contract_pct += pct
        elif h.get("is_cex"):
            p.cex_pct += pct
        elif h.get("is_dex"):
            p.dex_pct += pct
        else:
            p.wallet_pct += pct

    # ── Top20 详情 ──
    for h in holders[:20]:
        p.top20_details.append({
            "rank": h.get("rank"),
            "address": h.get("wallet_address", ""),
            "hold_pct": h.get("hold_percentage", 0),
            "buy_usd": h.get("buy_amt_usd", 0) or 0,
            "sell_usd": h.get("sell_amt_usd", 0) or 0,
            "dex_ratio": h.get("dex_ratio"),
            "is_cex": h.get("is_cex", 0),
            "is_contract": h.get("is_contract", 0),
            "is_dex": h.get("is_dex", 0),
            "label": h.get("address_label", "") or "",
            "acc_signals": h.get("acc_signals", "") or "",
            "is_accumulating": h.get("is_accumulating", 0),
            "h48_in": h.get("recent_48h_in", 0) or 0,
            "h48_out": h.get("recent_48h_out", 0) or 0,
        })

    # ── 95% 覆盖 ──
    cum = 0.0
    for h in holders:
        cum += h.get("hold_percentage", 0) or 0
        p.cov95_count += 1
        p.cov95_addresses.append({
            "rank": h.get("rank"),
            "address": h.get("wallet_address", ""),
            "hold_pct": h.get("hold_percentage", 0),
            "cumulative": round(cum, 4),
            "type": _addr_type(h),
            "label": h.get("address_label", "") or "",
            "h48_in": h.get("recent_48h_in", 0) or 0,
            "h48_out": h.get("recent_48h_out", 0) or 0,
        })
        if cum >= 95.0:
            break

    return p


def _addr_type(h: dict) -> str:
    if h.get("is_cex"):
        return "CEX"
    if h.get("is_dex"):
        return "DEX"
    if h.get("is_contract"):
        return "CONTRACT"
    return "WALLET"
