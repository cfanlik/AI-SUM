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
unified-scan — 入口
用法:
  python unified-scan/run.py                        # 全库扫描
  python unified-scan/run.py --symbol AGT           # 单币诊断
  python unified-scan/run.py --offline              # 不联网
"""
import sys
import os
import time
import argparse
import json

sys.path.insert(0, os.path.dirname(__file__))

import config
import db_loader
from analyzers.diamond_checker import check_diamond
from analyzers.diff_analyzer import compute_diff, check_a2, check_a3
from analyzers.cex_flow import analyze_cex_flow
from analyzers.holder_profiler import profile_holders
from analyzers.drift_detector import detect_drift
from analyzers.concentration import check_concentration
from analyzers.market_context import check_market
from verdict_engine import evaluate, UnifiedResult
from report_generator import print_terminal_report, generate_md_report


def _analyze_token(conn, token: dict, online: bool = True) -> UnifiedResult | None:
    """单代币完整分析流程。"""
    chain = token["chain"]
    addr = token["token_address"]
    sym = token.get("token_symbol", "?")

    # 1. 快照列表
    snap_times = db_loader.load_snapshot_times(conn, chain, addr)
    if len(snap_times) < config.MIN_SNAPSHOTS:
        return None

    latest_snap = snap_times[-1]
    earliest_snap = snap_times[0]

    # 2. 最新快照全300地址 → A1(钻石) + S1(集中度)
    latest_holders = db_loader.load_snapshot_holders(conn, chain, addr, latest_snap, 300)
    if len(latest_holders) < 5:
        return None

    diamond = check_diamond(latest_holders)
    concentration = check_concentration(latest_holders)

    # 3. 相邻快照 diff → A2(聚合) + A3(新鲸)
    a2 = {"triggered": False, "level": None}
    a3 = {"triggered": False, "level": None}
    acc_hold_pct = 0.0
    if len(snap_times) >= 2:
        prev_snap = snap_times[-2]
        prev_holders = db_loader.load_snapshot_holders(conn, chain, addr, prev_snap, 300)
        diff = compute_diff(prev_holders, latest_holders, prev_snap, latest_snap)
        a2 = check_a2(diff)
        a3 = check_a3(diff)
        acc_hold_pct = diff.acc_hold_new

    # 4. 全快照统计 → A4(CEX流出) + D1(CEX流入)
    stats = db_loader.load_snapshot_stats_series(conn, chain, addr)
    cex = analyze_cex_flow(stats)

    # 5. Top30 → D2(出货者画像)
    top30 = latest_holders[:config.TOP_HOLDERS_N]
    profiler = profile_holders(top30)

    # 6. 首末快照 → D3(被动漂移)
    first_holders = db_loader.load_snapshot_holders(conn, chain, addr, earliest_snap, 20)
    drift = detect_drift(first_holders, latest_holders)

    # 7. Gecko → S2(M/L) + S3(买卖人数比)
    gecko = db_loader.load_gecko_latest(conn, chain, addr)
    if not online:
        config.S3_ENABLED = False
    market = check_market(gecko, chain, addr)
    config.S3_ENABLED = True  # 恢复

    # 8. 综合裁决
    token_info = {**token, "snap_count": len(snap_times)}
    result = evaluate(diamond, a2, a3, cex, profiler, drift, concentration, market, token_info)
    result.acc_hold_pct = acc_hold_pct

    return result


def run_full_scan(offline: bool = False) -> list[UnifiedResult]:
    t0 = time.time()
    print(f"\n[unified-scan] 全库扫描启动")
    print(f"  源库: {config.SRC_DB_PATH}")
    print(f"  模式: {'纯离线' if offline else '联网(Gecko Pool)'}")

    conn = db_loader.get_connection()
    tokens = db_loader.load_all_tokens(conn)
    print(f"  代币总数: {len(tokens)}")

    results = []
    gecko_calls = 0
    for i, token in enumerate(tokens):
        # S3 联网控制: 对前 Top 40 候选联网
        do_online = not offline and gecko_calls < 40
        r = _analyze_token(conn, token, online=do_online)
        if r:
            results.append(r)
            if do_online and r.verdict != "NEUTRAL":
                gecko_calls += 1
        if (i + 1) % 50 == 0:
            print(f"  进度: {i+1}/{len(tokens)}", flush=True)

    elapsed = time.time() - t0

    # 终端报告
    print_terminal_report(results, elapsed)

    # MD 报告
    path = generate_md_report(results)
    print(f"\n  报告已保存: {path}")

    # 持久化
    scan_time = datetime.now().isoformat()
    prev_verdicts = db_loader.load_previous_scan_verdicts(conn)
    new_alerts = []
    for r in results:
        row = {
            "scan_time": scan_time,
            "chain": r.chain, "token_address": r.token_address,
            "token_symbol": r.token_symbol,
            "acc_score": r.acc_score, "dist_score": r.dist_score,
            "struct_risk": r.struct_risk, "verdict": r.verdict,
            "acc_cnt": r.acc_cnt, "acc_hold_pct": r.acc_hold_pct,
            "dex_verified_pct": r.dex_verified_pct,
            "cex_hold_pct": r.cex_hold_pct, "cex_delta_pct": r.cex_delta_pct,
            "top2_hold": r.top2_hold, "top10_hold": r.top10_hold,
            "institutional_hold": r.institutional_hold,
            "hidden_whale_cnt": r.hidden_whale_cnt,
            "mcap_liq_ratio": r.mcap_liq_ratio,
            "triggered_signals": r.triggered,
            "signal_details": r.signal_details,
        }
        db_loader.save_unified_result(conn, row)

        key = f"{r.chain}:{r.token_address}"
        if r.verdict in ("DIAMOND", "STRONG_ACC", "WHALE_DUMP"):
            prev = prev_verdicts.get(key)
            if prev != r.verdict:
                new_alerts.append(f"{r.token_symbol}({r.verdict})")

    # 每日汇总
    counts = {}
    for r in results:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1

    summary = {
        "scan_date": datetime.now().strftime("%Y-%m-%d"),
        "total_tokens": len(results),
        "diamond_count": counts.get("DIAMOND", 0),
        "strong_acc_count": counts.get("STRONG_ACC", 0),
        "dist_count": counts.get("SLOW_DISTRIBUTION", 0),
        "whale_dump_count": counts.get("WHALE_DUMP", 0),
        "new_alerts": new_alerts,
        "removed_alerts": [],
    }
    db_loader.save_daily_summary(conn, summary)
    conn.commit()
    conn.close()

    print(f"\n  持久化完成: unified_results + daily_summary")
    # ── [P0.1] 持久化到 select-sum.db ──
    try:
        import sys as _sys; _sys.path.insert(0, "/opt/AI-SUM")
        from persist_helper import save_unified; save_unified(results)
    except Exception as _e:
        print(f"  [persist] unified 保存失败: {_e}")

    if new_alerts:
        print(f"  新增预警: {', '.join(new_alerts)}")

    return results


def run_single(symbol: str = None, address: str = None, chain: str = "bsc"):
    print(f"\n[unified-scan] 单币诊断: {symbol or address}")
    conn = db_loader.get_connection()
    tokens = db_loader.load_all_tokens(conn)

    target = None
    for t in tokens:
        if symbol and t.get("token_symbol", "").upper() == symbol.upper():
            if not chain or t["chain"] == chain:
                target = t
                break
        elif address and t["token_address"].lower() == address.lower():
            target = t
            break

    if not target:
        print(f"  未找到代币")
        conn.close()
        return

    r = _analyze_token(conn, target, online=True)
    conn.close()
    if not r:
        print(f"  数据不足")
        return

    icon = {"DIAMOND": "💎", "WHALE_DUMP": "🐋", "SLOW_DISTRIBUTION": "🔴",
            "STRONG_ACC": "🟢", "MODERATE_ACC": "🔵", "MIXED": "🟡", "NEUTRAL": "⚪"}
    print(f"\n  {icon.get(r.verdict,'')} {r.token_symbol} → {r.verdict}")
    print(f"  ACC: {r.acc_score:.1f}% | DIST: {r.dist_score:.1f}% | STRUCT: {r.struct_risk:.1f}%")
    print(f"  机构控盘: {r.institutional_hold:.1f}% | DEX真金: {r.dex_verified_pct:.1f}%")
    print(f"  CEX: {r.cex_hold_pct:.1f}% (Δ{r.cex_delta_pct:+.1f}%)")
    print(f"  Top2: {r.top2_hold:.1f}% | Top10: {r.top10_hold:.1f}% | M/L: {r.mcap_liq_ratio:.1f}x")
    print(f"  触发: {', '.join(r.triggered) if r.triggered else '无'}")


from datetime import datetime

def main():
    parser = argparse.ArgumentParser(description="unified-scan 统一扫描引擎")
    parser.add_argument("--symbol", "-s", help="单币诊断(symbol)")
    parser.add_argument("--address", "-a", help="合约地址")
    parser.add_argument("--chain", "-c", default="bsc")
    parser.add_argument("--offline", action="store_true", help="纯离线")
    args = parser.parse_args()

    if args.symbol:
        run_single(symbol=args.symbol, chain=args.chain)
    elif args.address:
        run_single(address=args.address, chain=args.chain)
    else:
        run_full_scan(offline=args.offline)


if __name__ == "__main__":
    main()
