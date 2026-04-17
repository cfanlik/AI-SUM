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
D3: 被动漂移检测
来源: bigcoin drift_detector
核心逻辑: 持仓比例上升 + 买入金额不变 = 其他人退出导致占比被动升高 = 庄控信号
"""
from __future__ import annotations
import config


def detect_drift(first_holders: list[dict], last_holders: list[dict]) -> dict:
    """
    对比首末快照 Top10 持仓变化。
    返回: {triggered, passive_drift_count, whale1_drift, whale2_drift, details}
    """
    result = {
        "triggered": False,
        "passive_drift_count": 0,
        "whale1_drift": 0.0,
        "whale1_buy_unchanged": False,
        "whale2_drift": 0.0,
        "whale2_buy_unchanged": False,
        "details": [],
    }

    if not first_holders or not last_holders:
        return result

    # 构建首快照地址→持仓映射（Top20）
    first_map = {}
    for h in first_holders[:20]:
        addr = h.get("wallet_address", "")
        first_map[addr] = {
            "hold": h.get("hold_percentage", 0) or 0,
            "buy": h.get("buy_amt_usd", 0) or 0,
        }

    # 遍历末快照 Top10，检测漂移
    for i, h in enumerate(last_holders[:10]):
        addr = h.get("wallet_address", "")
        hold_now = h.get("hold_percentage", 0) or 0
        buy_now = h.get("buy_amt_usd", 0) or 0
        is_infra = (h.get("is_cex") or h.get("is_contract") or h.get("is_supernode"))

        prev = first_map.get(addr)
        if prev is None or is_infra:
            continue

        hold_prev = prev["hold"]
        buy_prev = prev["buy"]
        delta = hold_now - hold_prev
        buy_same = abs(buy_now - buy_prev) < config.D3_BUY_TOLERANCE

        if i == 0:
            result["whale1_drift"] = round(delta, 2)
            result["whale1_buy_unchanged"] = buy_same and delta > 0

        if i == 1:
            result["whale2_drift"] = round(delta, 2)
            result["whale2_buy_unchanged"] = buy_same and delta > 0

        # 被动漂移: 持仓涨>3% + 买入不变
        if delta > config.D3_DRIFT_MIN and buy_same:
            result["passive_drift_count"] += 1
            result["details"].append({
                "rank": i + 1,
                "addr": addr[:16] + "...",
                "hold_first": round(hold_prev, 2),
                "hold_last": round(hold_now, 2),
                "drift": round(delta, 2),
            })

    # 触发条件: 被动漂移≥3 或 Top1/2 大幅漂移
    result["triggered"] = (
        result["passive_drift_count"] >= config.D3_MULTI_DRIFT_MIN
        or (result["whale1_drift"] > 5.0 and result["whale1_buy_unchanged"])
    )

    return result
