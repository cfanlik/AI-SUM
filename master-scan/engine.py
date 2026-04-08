"""
AI-SUM V5 — 主调度引擎
流水线：加载代币 → 时序对齐 → 模式识别 → 更新 Watchlist → 生成报告
"""
from __future__ import annotations

import time
from datetime import datetime

import config, db_loader
from time_series_aligner import batch_build_time_series, build_time_series
from pattern_detector import scan_all, detect
from watchlist_tracker import update_watchlist, print_watchlist_table
from report_generator import generate_md_report, print_terminal_report


def run_full_scan(
    use_cache: bool = True,
    verbose: bool = True,
    save_report: bool = True,
) -> dict:
    """
    全库扫描主流程。
    返回：{report_path, stats}
    """
    t0 = time.time()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if verbose:
        print(f"\n[AI-SUM V5] 全库扫描启动 — {now}")
        print(f"  源库: {config.SRC_DB_PATH}")
        print(f"  分析库: {config.SUM_DB_PATH}")

    conn = db_loader.get_connection()

    # Step 1: 加载全部代币
    if verbose:
        print("  Step 1/5: 加载代币列表...", end=" ")
    tokens = db_loader.load_all_tokens(conn)
    if verbose:
        print(f"{len(tokens)} 个代币")

    # Step 2: 时序对齐
    if verbose:
        print("  Step 2/5: 时序对齐（Δ diff 计算）...")
    ts_list = batch_build_time_series(conn, tokens, use_cache=use_cache, verbose=verbose)
    if verbose:
        print(f"  -> {len(ts_list)} 个代币有效（≥2 快照）")

    # Step 3: 模式识别
    if verbose:
        print("  Step 3/5: 行为模式识别...", end=" ")
    all_results = scan_all(ts_list)
    signaled = [r for r in all_results if r.has_signal]
    reds     = [r for r in signaled if r.is_red_or_above]
    yellows  = [r for r in signaled if not r.is_red_or_above]
    if verbose:
        print(f"红色 {len(reds)} | 黄色 {len(yellows)}")

    # Step 4: 更新 Watchlist
    if verbose:
        print("  Step 4/5: 更新 Watchlist...", end=" ")
    wl_stats = update_watchlist(conn, all_results)
    if verbose:
        print(f"新增 {wl_stats['new_added']} / EXPIRED {wl_stats['expired']} / ACTIVE {wl_stats['total_active']}")

    # Step 5: 生成报告
    if verbose:
        print("  Step 5/5: 生成雷达报...")

    # 加载 Watchlist 和 V4 参照
    watchlist_items = db_loader.load_active_watchlist(conn)
    top10_v4 = db_loader.load_v4_agg_stats(conn)[:10]

    scan_start_ts = now
    print_terminal_report(all_results, wl_stats, top10_v4, scan_start_ts)

    report_path = None
    if save_report:
        report_path = generate_md_report(
            all_results=all_results,
            watchlist_items=watchlist_items,
            watchlist_stats=wl_stats,
            top10_v4=top10_v4,
        )
        if verbose:
            print(f"  报告已保存: {report_path}")

    # 记录运行
    db_loader.record_scan_run(
        conn=conn,
        tokens_scanned=len(tokens),
        red_alerts=len(reds),
        yellow_alerts=len(yellows),
        new_watchlist=wl_stats["new_added"],
        report_path=report_path,
    )

    elapsed = time.time() - t0
    if verbose:
        print(f"\n  ✓ 完成，耗时 {elapsed:.2f}s")

    conn.close()
    return {"report_path": report_path, "stats": wl_stats, "elapsed": elapsed}


def run_single_token(
    chain: str,
    token_address: str,
    backtest: bool = False,
) -> None:
    """
    单代币深度分析模式。
    输出该代币所有快照的 diff 序列和模式检测结果（适用于复盘）。
    """
    conn = db_loader.get_connection()
    tokens = db_loader.load_all_tokens(conn)

    # 查找目标代币
    target = None
    for t in tokens:
        if t["token_address"].lower() == token_address.lower() and t["chain"] == chain:
            target = t
            break

    if target is None:
        print(f"  ❌ 未找到代币: {chain}/{token_address}")
        conn.close()
        return

    sym = target.get("token_symbol", "?")
    print(f"\n{'='*70}")
    print(f"  🔍 单代币深度分析: {sym} ({chain})")
    print(f"  地址: {token_address}")
    print(f"{'='*70}")

    snap_times = db_loader.load_snapshot_times(conn, chain, token_address)
    print(f"  快照总数: {len(snap_times)}")
    if not snap_times:
        print("  ❌ 无快照数据")
        conn.close()
        return

    # 构建时序（不限窗口大小 = 全量 diff）
    import config as cfg
    orig_window = cfg.DEFAULT_SNAP_WINDOW
    cfg.DEFAULT_SNAP_WINDOW = len(snap_times)  # 临时扩展到全量

    ts = build_time_series(conn, target, use_cache=False)
    cfg.DEFAULT_SNAP_WINDOW = orig_window       # 还原

    if ts is None or not ts.diffs:
        print("  ❌ 无法构建时序（快照 < 2）")
        conn.close()
        return

    # 逐 diff 输出
    for i, diff in enumerate(ts.diffs):
        from pattern_detector import detect_pattern_a, detect_pattern_b, detect_pattern_c
        a_level, a_d = detect_pattern_a(diff)
        b_level, b_d = detect_pattern_b(diff)
        c_level, c_d, c_met = detect_pattern_c(diff)

        prefix = f"  [{i+1}/{len(ts.diffs)}]"
        gap_warn = " ⚠️时差大" if diff.gap_warning else ""
        print(f"\n{prefix} {diff.t_old[:16]} → {diff.t_new[:16]}  ({diff.hours_gap:.1f}h{gap_warn})")
        print(f"    换手率: {diff.roster_turnover_pct*100:.1f}% | 新acc: {diff.new_acc_count} | Δacc: {diff.delta_acc_count:+d}")
        print(f"    acc_hold: {diff.acc_hold_old:.3f}% → {diff.acc_hold_new:.3f}% (Δ{diff.delta_acc_hold:+.3f}%)")
        print(f"    只买不卖: {diff.latest_only_buy_pct*100:.1f}% | 历史持仓中位数: {diff.historical_acc_hold_median:.3f}%")

        signals = []
        if a_level:
            signals.append(f"A({a_level})")
        if b_level:
            signals.append(f"B({b_level})")
        if c_level:
            signals.append(f"C({c_level},{c_met}/4条件)")
        if signals:
            print(f"    ★ 触发信号: {' + '.join(signals)}")
        else:
            print("    - 无信号")

    # 最新 diff 模式检测
    result = detect(ts)
    if result:
        print(f"\n  📌 最新快照综合信号: {result.composite_level or '无'}")
        if result.triggered_patterns:
            print(f"  触发模式: {', '.join(result.triggered_patterns)}")

    print(f"\n{'='*70}\n")
    conn.close()


def show_watchlist(conn=None) -> None:
    """打印当前 ACTIVE watchlist。"""
    _own_conn = conn is None
    if _own_conn:
        conn = db_loader.get_connection()
    print_watchlist_table(conn)
    if _own_conn:
        conn.close()
