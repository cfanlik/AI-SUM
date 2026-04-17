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
S1: 极端集中度检测
来源: bigcoin concentration_profiler
"""
from __future__ import annotations
import config


def check_concentration(holders: list[dict]) -> dict:
    r = {"triggered": False, "top2_hold": 0.0, "top5_hold": 0.0,
         "top10_hold": 0.0, "top20_hold": 0.0,
         "top2_avg_dex": -1.0, "top2_all_wallet": False,
         "contract_pct": 0.0, "cex_pct": 0.0}
    if not holders:
        return r

    for i, h in enumerate(holders):
        pct = h.get("hold_percentage", 0) or 0
        if i < 2: r["top2_hold"] += pct
        if i < 5: r["top5_hold"] += pct
        if i < 10: r["top10_hold"] += pct
        if i < 20: r["top20_hold"] += pct

    dex_vals = []
    all_wallet = True
    for h in holders[:2]:
        dr = h.get("dex_ratio")
        if dr is not None: dex_vals.append(dr)
        if h.get("is_cex") or h.get("is_contract") or h.get("is_dex"):
            all_wallet = False
    r["top2_avg_dex"] = sum(dex_vals) / len(dex_vals) if dex_vals else -1
    r["top2_all_wallet"] = all_wallet

    for h in holders[:20]:
        pct = h.get("hold_percentage", 0) or 0
        if h.get("is_contract"): r["contract_pct"] += pct
        elif h.get("is_cex"): r["cex_pct"] += pct

    for k in ["top2_hold","top5_hold","top10_hold","top20_hold","contract_pct","cex_pct"]:
        r[k] = round(r[k], 2)

    # S1 触发条件:
    # 路径A (原始): top2 都是普通钱包 + dex_ratio 极低
    # 路径B (新增): top1 为合约/CEX 持仓 >70% → 极端锁仓集中
    path_a = (r["top2_hold"] > config.S1_TOP2_HOLD
              and 0 <= r["top2_avg_dex"] < config.S1_TOP2_DEX_MAX
              and r["top2_all_wallet"])

    # 路径B: 单一地址（合约或CEX）持仓超70%
    top1 = holders[0] if holders else {}
    top1_pct = top1.get("hold_percentage", 0) or 0
    top1_locked = top1.get("is_contract") or top1.get("is_cex")
    path_b = top1_pct > 70.0 and top1_locked

    r["triggered"] = path_a or path_b
    return r
