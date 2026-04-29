"""
whale-scan — 入口
用法:
  python3 bigcoin/run.py                           # 全库扫描
  python3 bigcoin/run.py --symbol RAVE             # 单币诊断
  python3 bigcoin/run.py --address 0x976... --chain bsc  # 合约地址诊断
  python3 bigcoin/run.py --top 30                  # 自定义报告条数
"""
import sys
import os
import time
import argparse

sys.path.insert(0, os.path.dirname(__file__))

import config
import db_loader
from concentration_profiler import build_concentration
from drift_detector import detect_drift
from whale_verdict import evaluate
from report_generator import (
    print_radar, print_single,
    save_md_radar, save_md_single,
)


def _analyze_token(conn, chain: str, addr: str, sym: str):
    """单代币完整分析流程"""
    snapshots = db_loader.load_snapshot_times(conn, addr)
    if len(snapshots) < config.MIN_SNAPSHOTS:
        return None

    first_snap = snapshots[0]
    last_snap = snapshots[-1]

    # 加载持仓数据
    latest_holders = db_loader.load_top_holders(conn, addr, last_snap, 300)
    first_holders = db_loader.load_top_holders(conn, addr, first_snap, 20)

    if len(latest_holders) < 5:
        return None

    # 集中度分析
    cp = build_concentration(latest_holders)

    # 漂移检测
    acc_stats = db_loader.load_acc_stats_series(conn, addr)
    dr = detect_drift(first_holders, latest_holders, acc_stats,
                      first_snap, last_snap, len(snapshots))

    # 评分 & Gecko
    scores = db_loader.load_latest_scores(conn, addr)
    gecko = db_loader.load_latest_gecko(conn, addr)

    # 裁决
    verdict = evaluate(chain, addr, sym, cp, dr, scores, gecko, latest_holders)

    # G2: LP<$10K
    if verdict.lp_usd > 0 and verdict.lp_usd < 10000:
        verdict.confidence = 0
        verdict.level = "CLEAN"
    # G3: dead pool
    _vol = (gecko or {}).get("volume_24h") or 0
    if verdict.vl_ratio < 0.01 and _vol < 100 and verdict.confidence > 0:
        verdict.confidence = 0
        verdict.level = "CLEAN"
    return verdict


def run_full_scan(top_n: int = 20):
    """全库扫描"""
    t0 = time.time()
    print(f"\n[whale-scan] 全库扫描启动")
    print(f"  源库: {config.SRC_DB_PATH}")

    conn = db_loader.get_connection()
    tokens = db_loader.load_all_tokens(conn)
    print(f"  代币总数: {len(tokens)}")

    results = []
    for tok in tokens:
        v = _analyze_token(conn, tok["chain"], tok["token_address"], tok["symbol"])
        if v:
            results.append(v)

    conn.close()
    elapsed = time.time() - t0
    print(f"  有效代币: {len(results)}, 耗时 {elapsed:.1f}s")

    # 排序
    results.sort(key=lambda v: v.confidence, reverse=True)

    # 终端输出
    config.TOP_N_REPORT = top_n
    print_radar(results, elapsed)

    # MD 报告
    path = save_md_radar(results)
    print(f"\n  报告已保存: {path}")
    try:
        import sys as _sys; _sys.path.insert(0, "/opt/AI-SUM")
        from persist_helper import save_whale; save_whale(results)
    except Exception as _e:
        print(f"  [persist] whale保存失败: {_e}")


    return results


def run_single(chain: str, addr: str, sym: str = "?"):
    """单币诊断"""
    print(f"\n[whale-scan] 单币诊断: {sym} ({chain})")

    conn = db_loader.get_connection()

    # 如果只给了 symbol，查找地址
    if addr == "?" and sym != "?":
        tokens = db_loader.load_all_tokens(conn)
        match = [t for t in tokens if t["symbol"].upper() == sym.upper()]
        if not match:
            print(f"  ❌ 未找到代币 {sym}")
            conn.close()
            return None
        if chain:
            match = [t for t in match if t["chain"] == chain]
        if not match:
            print(f"  ❌ 未找到代币 {sym} 在链 {chain}")
            conn.close()
            return None
        addr = match[0]["token_address"]
        chain = match[0]["chain"]
        sym = match[0]["symbol"]

    v = _analyze_token(conn, chain, addr, sym)
    conn.close()

    if v is None:
        print(f"  ❌ 数据不足（快照<{config.MIN_SNAPSHOTS}或持仓<5）")
        return None

    print_single(v)
    path = save_md_single(v)
    print(f"\n  报告已保存: {path}")
    return v


def main():
    parser = argparse.ArgumentParser(description="whale-scan 庄控扫描器")
    parser.add_argument("--symbol", "-s", help="单币诊断 (symbol)")
    parser.add_argument("--address", "-a", help="合约地址")
    parser.add_argument("--chain", "-c", default="bsc", help="链 (默认 bsc)")
    parser.add_argument("--top", "-t", type=int, default=20, help="报告显示条数")
    args = parser.parse_args()

    if args.symbol:
        run_single(args.chain, "?", args.symbol)
    elif args.address:
        run_single(args.chain, args.address)
    else:
        run_full_scan(args.top)


if __name__ == "__main__":
    main()
