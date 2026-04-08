"""
AI-SUM V5 — 高价值对象追踪框架
- 管理 watchlist 生命周期：触发进入 → 持续更新 → 自动退出
- 对有信号的 PatternResult 自动写入 select-sum.db/watchlist
- 对 ACTIVE 但本次无信号的代币递增 no_signal 计数，超限 EXPIRED
"""
from __future__ import annotations

from typing import Optional

import db_loader, config
from pattern_detector import PatternResult, LEVEL_NONE


def _build_trigger_detail(r: PatternResult) -> dict:
    """将 PatternResult 的关键指标序列化为 watchlist.trigger_detail JSON。"""
    return {
        "composite_level": r.composite_level,
        "triggered_patterns": r.triggered_patterns,
        "roster_turnover_pct": round(r.roster_turnover_pct * 100, 1),
        "hours_gap": round(r.hours_gap, 1),
        "new_acc_count": r.new_acc_count,
        "new_acc_only_buy": r.new_acc_only_buy,
        "latest_only_buy_pct": round(r.latest_only_buy_pct * 100, 1),
        "acc_hold_new_pct": round(r.acc_hold_new, 3),
        "delta_acc_hold": round(r.delta_acc_hold, 3),
        "delta_acc_count": r.delta_acc_count,
        "acc_count_new": r.acc_count_new,
        "pattern_a": r.pattern_a_detail,
        "pattern_b": r.pattern_b_detail,
        "pattern_c": r.pattern_c_detail,
        "snap_count": r.snap_count,
        "latest_snapshot": r.latest_snapshot,
    }


def update_watchlist(
    conn,
    all_results: list[PatternResult],
) -> dict:
    """
    根据本次扫描结果更新 watchlist。
    
    逻辑：
      1. 有信号的代币 → upsert watchlist（新增或重置）
      2. ACTIVE 但本次无信号的代币 → 递增 no_signal 计数，超限 EXPIRED
    
    返回统计摘要：
      {new_added, updated, expired, total_active}
    """
    stats = {"new_added": 0, "updated": 0, "expired": 0}

    # 本次有信号的 key 集合
    signaled_keys: set[tuple] = set()

    for r in all_results:
        if not r.has_signal:
            continue
        signaled_keys.add((r.chain, r.token_address))
        is_new = db_loader.upsert_watchlist(
            conn=conn,
            chain=r.chain,
            token_address=r.token_address,
            token_symbol=r.token_symbol,
            trigger_pattern="+".join(r.triggered_patterns),
            signal_level=r.composite_level,
            trigger_detail=_build_trigger_detail(r),
        )
        if is_new:
            stats["new_added"] += 1
        else:
            stats["updated"] += 1

    # 对 ACTIVE 但本次无信号的代币递增计数
    active_list = db_loader.load_active_watchlist(conn)
    for item in active_list:
        key = (item["chain"], item["token_address"])
        if key not in signaled_keys:
            cnt = db_loader.increment_no_signal(conn, item["chain"], item["token_address"])
            if cnt >= config.WATCHLIST_EXPIRE_SCANS:
                stats["expired"] += 1

    # 重新统计 ACTIVE 数量
    stats["total_active"] = len(db_loader.load_active_watchlist(conn))
    return stats


def dismiss(conn, chain: str, token_address: str, notes: str = None) -> None:
    """人工标记误报：DISMISSED。"""
    db_loader.update_watchlist_status(conn, chain, token_address, "DISMISSED", notes)
    print(f"  ✓ {chain}/{token_address[:10]}... 已标记为 DISMISSED")


def mark_pumped(conn, chain: str, token_address: str, notes: str = None) -> None:
    """标记已拉盘：PUMPED。"""
    db_loader.update_watchlist_status(conn, chain, token_address, "PUMPED", notes)
    print(f"  ✓ {chain}/{token_address[:10]}... 已标记为 PUMPED")


def add_note(conn, chain: str, token_address: str, notes: str) -> None:
    """为 watchlist 记录添加人工备注（不改变状态）。"""
    conn.execute(
        "UPDATE watchlist SET notes=? WHERE chain=? AND token_address=?",
        (notes, chain, token_address),
    )
    conn.commit()
    print(f"  ✓ {chain}/{token_address[:10]}... 备注已更新")


def print_watchlist_table(conn) -> None:
    """打印当前 ACTIVE watchlist 到终端。"""
    items = db_loader.load_active_watchlist(conn)
    if not items:
        print("  Watchlist 为空（无 ACTIVE 追踪对象）")
        return

    print(f"\n{'='*90}")
    print(f"  📋 Watchlist — ACTIVE 追踪对象 ({len(items)} 个)")
    print(f"{'='*90}")
    header = f"{'代币':<10} {'链':<5} {'级别':<10} {'触发模式':<20} {'进入时间':<22} {'连续无信号':>8}"
    print(header)
    print("-" * 90)

    for item in items:
        sym = (item["token_symbol"] or "?")[:9]
        chain = (item["chain"] or "")[:4]
        level = item["signal_level"] or ""
        pattern = (item["trigger_pattern"] or "")[:18]
        added = (item["added_at"] or "")[:19]
        no_sig = item["consecutive_no_signal"] or 0

        # 级别着色标记
        level_tag = {
            "EXTREME": "🔴🔴🔴", "CRITICAL": "🔴🔴",
            "RED": "🔴", "YELLOW": "🟡",
        }.get(level, level)

        print(f"  {sym:<10} {chain:<5} {level_tag:<10} {pattern:<20} {added:<22} {no_sig:>8}")

    print(f"{'='*90}\n")
