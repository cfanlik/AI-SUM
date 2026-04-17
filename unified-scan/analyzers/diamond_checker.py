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
A1: 钻石绞杀检测
来源: master-scan pattern_detector.detect_pattern_e
条件: institutional_hold_v8 ≥ 85% AND dex_verified_pct ≥ 85%
"""
from __future__ import annotations
import config


def check_diamond(holders: list[dict]) -> dict:
    """
    从最新快照的全300地址计算钻石绞杀指标。
    返回: {triggered, institutional_hold, dex_verified_pct, hidden_whale_count, acc_count}
    """
    if not holders:
        return {"triggered": False, "institutional_hold": 0, "dex_verified_pct": 0,
                "hidden_whale_count": 0, "acc_count": 0}

    # ── 机构控盘率 ──
    infra_hold = 0.0
    big_holder_hold = 0.0
    hidden_whales = 0
    acc_count = 0
    acc_addrs = []

    for h in holders:
        pct = h.get("hold_percentage", 0) or 0
        is_infra = (h.get("is_cex") or h.get("is_contract") or h.get("is_supernode"))

        if is_infra:
            infra_hold += pct
        elif pct >= config.HIDDEN_WHALE_HOLD_THRESHOLD:
            big_holder_hold += pct
            hidden_whales += 1

        if h.get("is_accumulating"):
            acc_count += 1
            acc_addrs.append(h)

    institutional_hold = infra_hold + big_holder_hold

    # ── DEX 真金率 ──
    if acc_count == 0:
        dex_verified_pct = 0.0
    else:
        # 构建 entity_id → has_dex 映射
        entity_dex = {}
        for h in acc_addrs:
            eid = h.get("entity_id") or ""
            dr = h.get("dex_ratio") or 0
            dr2 = h.get("dex_ratio_hop2") or 0
            gv = h.get("gmgn_verified") or 0
            has_dex = (dr >= 0.5) or (dr2 >= 0.5) or (gv >= 1)
            if eid and has_dex:
                entity_dex[eid] = True

        verified = 0
        for h in acc_addrs:
            dr = h.get("dex_ratio") or 0
            dr2 = h.get("dex_ratio_hop2") or 0
            gv = h.get("gmgn_verified") or 0
            eid = h.get("entity_id") or ""

            if (dr >= 0.5) or (dr2 >= 0.5) or (gv >= 1):
                verified += 1
            elif eid and entity_dex.get(eid):
                verified += 1

        dex_verified_pct = round(verified / acc_count * 100, 1)

    triggered = (
        institutional_hold >= config.DIAMOND_INST_THRESHOLD
        and dex_verified_pct >= config.DIAMOND_DEX_THRESHOLD
    )

    return {
        "triggered": triggered,
        "institutional_hold": round(institutional_hold, 1),
        "dex_verified_pct": dex_verified_pct,
        "hidden_whale_count": hidden_whales,
        "acc_count": acc_count,
    }
