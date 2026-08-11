#!/usr/bin/env python3
"""
cost-basis-scan 主入口
第五套分析框架：基于 gmgn_avg_price 成本数据的吸筹/出货检测

用法:
    python3 run.py                          # 全库扫描
    python3 run.py --symbol GWEI            # 单币诊断
    python3 run.py --address 0x40b... --chain bsc  # 合约地址
"""
from __future__ import annotations
import sys
import os
import argparse
import logging
from datetime import datetime
from pathlib import Path

# 确保模块路径
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import db_loader
import cost_profiler
import gravity_tracker
import verdict_engine
import report_generator

# 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("cost-basis-scan")


def scan_single(
    conn,
    chain: str,
    token_address: str,
    token_symbol: str,
    scan_time: str,
    verbose: bool = False,
) -> verdict_engine.VerdictResult | None:
    """扫描单个代币"""

    # 1. 加载 Gecko 现价
    gecko = db_loader.load_gecko_latest(conn, chain, token_address)
    if not gecko or gecko.price_usd <= 0:
        if verbose:
            logger.info(f"  {token_symbol}: G2 跳过 (无 Gecko 价格)")
        return None  # G2 门控

    price = gecko.price_usd

    # 2. 加载成本数据
    holders = db_loader.load_cost_holders(conn, chain, token_address, price)
    if len(holders) < config.G1_MIN_COST_HOLDERS:
        if verbose:
            logger.info(f"  {token_symbol}: G1 跳过 (成本地址={len(holders)} < {config.G1_MIN_COST_HOLDERS})")
        return None  # G1 门控

    # 3. 加载上一快照
    prev_holders = db_loader.load_previous_cost_holders(conn, chain, token_address)

    # 4. 构建成本画像
    profile = cost_profiler.build_profile(
        holders=holders,
        prev_holders=prev_holders,
        gecko_price=price,
        chain=chain,
        token_address=token_address,
    )

    # 5. 成本重心漂移
    current_gravity = gravity_tracker.compute_gravity(holders)
    gravity = gravity_tracker.detect_gravity_drift(
        conn, chain, token_address, current_gravity, price,
    )

    # 6. watchlist 跃迁检测
    c1_hit = profile.underwater_adding_count >= 2
    c4_hit = profile.windfall_selling_count >= 1
    transition = gravity_tracker.check_watchlist_transition(
        conn, chain, token_address, c1_hit, c4_hit,
    )

    # 7. 裁决
    vr = verdict_engine.evaluate(
        profile=profile,
        gecko=gecko,
        gravity=gravity,
        transition=transition,
        token_symbol=token_symbol,
    )

    # ─── 锁仓聚合计算 ───
    eoa_holders = [h for h in holders if h.is_cex == 0 and h.is_dex == 0 and h.is_contract == 0]
    total_acc = len(eoa_holders)
    ob_cnt = 0
    ob_hp = 0.0
    s1_cnt = 0
    s1_hp = 0.0
    s3_cnt = 0
    s3_hp = 0.0
    for h in eoa_holders:
        buy_amt = h.gmgn_buy_cost_usd
        sell_amt = h.sell_amt_usd
        hold_pct = h.hold_percentage
        if sell_amt == 0.0 and buy_amt > 0:
            ob_cnt += 1
            ob_hp += hold_pct
        if sell_amt < buy_amt * 0.01 and buy_amt > 0:
            s1_cnt += 1
            s1_hp += hold_pct
        if sell_amt < buy_amt * 0.03 and buy_amt > 0:
            s3_cnt += 1
            s3_hp += hold_pct
    
    vr.only_buy_cnt = ob_cnt
    vr.only_buy_pct = (ob_cnt / total_acc * 100) if total_acc > 0 else 0.0
    vr.only_buy_hold_pct = ob_hp
    vr.sell_under_1_cnt = s1_cnt
    vr.sell_under_1_pct = (s1_cnt / total_acc * 100) if total_acc > 0 else 0.0
    vr.sell_under_1_hold_pct = s1_hp
    vr.sell_under_3_cnt = s3_cnt
    vr.sell_under_3_pct = (s3_cnt / total_acc * 100) if total_acc > 0 else 0.0
    vr.sell_under_3_hold_pct = s3_hp

    # 8. 保存到 select-sum.db
    db_loader.save_scan_result(conn, {
        "chain": chain,
        "token_address": token_address,
        "scan_time": scan_time,
        "token_symbol": token_symbol,
        "verdict": vr.verdict,
        "acc_pct": vr.acc_pct,
        "dist_pct": vr.dist_pct,
        "vwap": vr.vwap,
        "gecko_price": vr.gecko_price,
        "cost_gravity": vr.cost_gravity,
        "gravity_drift_ratio": vr.gravity_drift_ratio,
        "windfall_pct": vr.windfall_pct,
        "underwater_pct": vr.deep_underwater_pct + vr.shallow_underwater_pct,
        "cost_cv": vr.cost_cv,
        "cost_holders_count": vr.cost_holders_count,
        "triggered_signals": vr.triggered_signals,
        "watchlist_ref": vr.watchlist_ref,
    })

    if verbose or vr.verdict != "NEUTRAL":
        logger.info(f"  {token_symbol}: {vr.verdict} (ACC={vr.acc_pct:.0f}% "
                     f"DIST={vr.dist_pct:.0f}% CV={vr.cost_cv:.3f} "
                     f"暴利={vr.windfall_pct:.1f}% 信号={vr.triggered_signals})")

    return vr


def main():
    parser = argparse.ArgumentParser(description="cost-basis-scan 成本基础扫描")
    parser.add_argument("--symbol", help="单币诊断 (symbol)")
    parser.add_argument("--address", help="合约地址诊断")
    parser.add_argument("--chain", default="bsc", help="链名 (默认 bsc)")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    args = parser.parse_args()

    scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 连接数据库
    conn = db_loader.get_connection()
    db_loader.ensure_tables(conn)

    # 加载代币列表
    all_tokens = db_loader.load_all_tokens(conn)
    logger.info(f"加载 {len(all_tokens)} 个代币")

    # 过滤
    if args.symbol:
        target = [t for t in all_tokens if t.token_symbol.upper() == args.symbol.upper()]
        if not target:
            logger.error(f"未找到代币: {args.symbol}")
            return
        tokens = target
        args.verbose = True
    elif args.address:
        target = [t for t in all_tokens
                   if t.token_address.lower() == args.address.lower()
                   and t.chain == args.chain]
        if not target:
            logger.error(f"未找到合约: {args.chain}/{args.address}")
            return
        tokens = target
        args.verbose = True
    else:
        tokens = all_tokens

    # 扫描
    results = []
    skipped_g1 = 0
    skipped_g2 = 0

    for i, t in enumerate(tokens, 1):
        if not args.verbose and i % 20 == 0:
            logger.info(f"进度: {i}/{len(tokens)}")

        vr = scan_single(
            conn, t.chain, t.token_address, t.token_symbol,
            scan_time, verbose=args.verbose,
        )

        if vr is None:
            # 区分 G1 和 G2
            gecko = db_loader.load_gecko_latest(conn, t.chain, t.token_address)
            if not gecko or gecko.price_usd <= 0:
                skipped_g2 += 1
            else:
                skipped_g1 += 1
        elif vr.gate_skipped:
            skipped_g1 += 1  # 门控跳过也计入
        else:
            results.append(vr)

    # 报告
    report_path = report_generator.generate_report(
        results=results,
        scan_time=scan_time,
        total_tokens=len(tokens),
        skipped_g1=skipped_g1,
        skipped_g2=skipped_g2,
    )

    conn.close()
    logger.info(f"扫描完成: {len(results)} 有效结果")


if __name__ == "__main__":
    main()
