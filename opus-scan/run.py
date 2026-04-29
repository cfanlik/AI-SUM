"""
opus-scan — 入口 (v2: 默认联网，两阶段扫描)
用法:
  python opus-scan/run.py                        # 全库扫描（DB快筛+Top候选联网增强）
  python opus-scan/run.py <address> <chain>      # 单代币深度诊断（自动联网）
  python opus-scan/run.py --top 20               # 自定义 Top N
  python opus-scan/run.py --offline              # 纯离线（不调 Gecko API）
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(__file__))

import config
import db_loader
from time_series_builder import build_time_series
from holder_profiler import build_profile
from web_researcher import research
from verdict_engine import evaluate
from report_generator import (
    print_leaderboard, print_single_verdict,
    save_md_leaderboard, save_md_single,
)


def _build_token_data(conn, chain, addr, sym, online=False, proxy=None):
    """单代币完整分析流程（共用逻辑）"""
    stats = db_loader.load_snapshot_stats_series(conn, chain, addr)
    ts = build_time_series(stats, chain, addr, sym)
    if ts is None:
        return None, None, None, None

    latest_snap = stats[-1]["snapshot_time"]
    earliest_snap = stats[0]["snapshot_time"]
    latest_holders = db_loader.load_top_holders(conn, chain, addr, latest_snap, config.TOP_HOLDERS_N)
    earliest_holders = db_loader.load_top_holders(conn, chain, addr, earliest_snap, config.TOP_HOLDERS_N)
    hp = build_profile(latest_holders, earliest_holders, latest_snap, earliest_snap)

    gecko_db = db_loader.load_gecko_latest(conn, chain, addr)
    mc = research(chain, addr, gecko_db, proxy=proxy, online=online)

    vr = evaluate(ts, hp, mc)
    return vr, ts, hp, mc


def run_full_scan(top_n: int = 10, offline: bool = False, proxy: str = None):
    """
    两阶段全库扫描:
      阶段1 — DB 快筛全部代币（~3s）
      阶段2 — 对 Top 候选调用 Gecko Pool API 增强（~30s）
    """
    t0 = time.time()
    config.TOP_N = top_n
    enrich_n = top_n * 2  # 对 Top N×2 的候选做联网增强

    print(f"\n[opus-scan] 全库扫描启动")
    print(f"  源库: {config.SRC_DB_PATH}")
    print(f"  模式: {'纯离线' if offline else 'DB快筛 + Top候选联网增强'}")

    conn = db_loader.get_connection()
    tokens = db_loader.load_all_tokens(conn)
    print(f"  代币总数: {len(tokens)}")

    # ── 阶段1: DB 快筛 ──
    print(f"  阶段1: DB 快筛...", end="", flush=True)
    t1 = time.time()
    results = []
    token_map = {}  # addr → token info，用于阶段2 重查
    for token in tokens:
        chain = token["chain"]
        addr = token["token_address"]
        sym = token.get("token_symbol", "?")

        vr, ts, hp, mc = _build_token_data(conn, chain, addr, sym, online=False, proxy=proxy)
        if vr is None:
            continue
        results.append(vr)
        token_map[f"{chain}:{addr}"] = token

    print(f" {len(results)} 个有效, 耗时 {time.time()-t1:.1f}s")

    if offline:
        # 纯离线模式，直接输出
        elapsed = time.time() - t0
        print_leaderboard(results, elapsed)
        path = save_md_leaderboard(results)
        print(f"  报告已保存: {path}")
        try:
            import sys as _sys; _sys.path.insert(0, "/opt/AI-SUM")
            from persist_helper import save_opus; save_opus(results)
        except Exception as _e:
            print(f"  [persist] opus 保存失败: {_e}")
        conn.close()
        return results

    # ── 阶段2: Top 候选联网增强 ──
    # 取吸筹 + 出货各 Top enrich_n 的候选（去重）
    top_acc_cands = sorted(results, key=lambda r: r.acc_confidence, reverse=True)[:enrich_n]
    top_dist_cands = sorted(results, key=lambda r: r.dist_confidence, reverse=True)[:enrich_n]

    # 合并去重
    enrich_keys = set()
    for r in top_acc_cands + top_dist_cands:
        enrich_keys.add(f"{r.chain}:{r.token_address}")

    print(f"  阶段2: 联网增强 {len(enrich_keys)} 个候选...", flush=True)
    t2 = time.time()

    enriched = {}
    done = 0
    for key in enrich_keys:
        chain, addr = key.split(":", 1)
        token = token_map.get(key)
        if not token:
            continue
        sym = token.get("token_symbol", "?")

        vr, _, _, _ = _build_token_data(conn, chain, addr, sym, online=True, proxy=proxy)
        if vr:
            enriched[key] = vr
        done += 1
        if done % 5 == 0:
            print(f"\r  阶段2: 联网增强 {done}/{len(enrich_keys)}...", end="", flush=True)

    print(f"\r  阶段2: 完成 {len(enriched)} 个联网增强, 耗时 {time.time()-t2:.1f}s        ")

    # 替换已增强的结果
    final_results = []
    for r in results:
        key = f"{r.chain}:{r.token_address}"
        if key in enriched:
            final_results.append(enriched[key])
        else:
            final_results.append(r)

    elapsed = time.time() - t0
    print_leaderboard(final_results, elapsed)

    path = save_md_leaderboard(final_results)
    print(f"  报告已保存: {path}")
    try:
        import sys as _sys; _sys.path.insert(0, "/opt/AI-SUM")
        from persist_helper import save_opus; save_opus(final_results)
    except Exception as _e:
        print(f"  [persist] opus保存失败: {_e}")
    conn.close()
    return final_results


def run_single_token(address: str, chain: str, offline: bool = False, proxy: str = None):
    """单代币深度诊断（默认联网）"""
    online = not offline
    print(f"\n[opus-scan] 单代币诊断: {chain}/{address[:12]}...")
    print(f"  模式: {'纯离线' if offline else '联网'}")

    conn = db_loader.get_connection()

    stats = db_loader.load_snapshot_stats_series(conn, chain, address)
    if len(stats) < 2:
        print("  ❌ 快照不足（< 2）")
        conn.close()
        return

    tokens = db_loader.load_all_tokens(conn)
    sym = "?"
    for t in tokens:
        if t["token_address"].lower() == address.lower() and t["chain"] == chain:
            sym = t.get("token_symbol", "?")
            break

    vr, _, _, _ = _build_token_data(conn, chain, address, sym, online=online, proxy=proxy)
    if vr is None:
        print("  ❌ 无法构建时序")
        conn.close()
        return

    print_single_verdict(vr)

    path = save_md_single(vr)
    print(f"  报告已保存: {path}")

    conn.close()
    return vr


def main():
    args = sys.argv[1:]

    offline = "--offline" in args
    args = [a for a in args if a != "--offline"]

    proxy = None
    if "--proxy" in args:
        idx = args.index("--proxy")
        if idx + 1 < len(args):
            proxy = args[idx + 1]
            args = args[:idx] + args[idx+2:]

    top_n = 10
    if "--top" in args:
        idx = args.index("--top")
        if idx + 1 < len(args):
            top_n = int(args[idx + 1])
            args = args[:idx] + args[idx+2:]

    if not args:
        run_full_scan(top_n=top_n, offline=offline, proxy=proxy)
    elif len(args) >= 2:
        run_single_token(args[0], args[1], offline=offline, proxy=proxy)
    else:
        print("用法:")
        print("  python opus-scan/run.py                        # 全库扫描（默认联网增强）")
        print("  python opus-scan/run.py <address> <chain>      # 单代币诊断（默认联网）")
        print("  python opus-scan/run.py --offline              # 纯离线（不调 API）")
        print("  python opus-scan/run.py --top 20               # 自定义 Top N")


if __name__ == "__main__":
    main()
