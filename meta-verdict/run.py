#!/usr/bin/env python3
"""
meta-verdict 主入口
五引擎仲裁 → 综合排名 + 生命周期状态

用法:
    python3 meta-verdict/run.py          # 全量仲裁
    python3 meta-verdict/run.py --symbol GWEI  # 单币诊断
"""
from __future__ import annotations
import sys
import os
import logging
import argparse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from collector import get_connection, ensure_tables, collect_all_tokens, save_meta_result
from arbitrator import run_arbitration, arbitrate
from report_generator import generate_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("meta-verdict")


def main():
    parser = argparse.ArgumentParser(description="meta-verdict 五引擎仲裁")
    parser.add_argument("--symbol", help="单币诊断")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    ensure_tables(conn)

    # 收集所有代币跨引擎数据
    all_data = collect_all_tokens(conn)
    logger.info(f"收集到 {len(all_data)} 个有信号代币（至少 1 个引擎命中）")

    # 单币过滤
    if args.symbol:
        all_data = [d for d in all_data if d.token_symbol.upper() == args.symbol.upper()]
        if not all_data:
            logger.error(f"未找到代币: {args.symbol}")
            conn.close()
            return
        args.verbose = True

    # 仲裁
    acc_list, dist_list = run_arbitration(all_data)

    # 保存仲裁结果
    for r in acc_list + dist_list:
        save_meta_result(conn, {
            "scan_time":      scan_time,
            "chain":          r.chain,
            "token_address":  r.token_address,
            "token_symbol":   r.token_symbol,
            "meta_score":     r.meta_score,
            "meta_verdict":   r.meta_verdict,
            "engine_hits":    r.engine_hits,
            "master_signal":  r.master_signal,
            "opus_verdict":   r.opus_verdict,
            "unified_signal": r.unified_signal,
            "whale_level":    r.whale_level,
            "cb_verdict":     r.cb_verdict,
            "stage":          r.stage,
        })

    # 更新 Phase 3 生命周期表
    _update_lifecycle(conn, acc_list + dist_list, scan_time)

    # 生成报告
    generate_report(acc_list, dist_list, len(all_data), scan_time)
    conn.close()


def _update_lifecycle(conn, results, scan_time: str):
    """Phase 3: 更新代币生命周期状态机"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS token_lifecycle (
            chain          TEXT NOT NULL,
            token_address  TEXT NOT NULL,
            token_symbol   TEXT,
            current_stage  TEXT DEFAULT 'NEUTRAL',
            prev_stage     TEXT DEFAULT '',
            stage_since    TEXT,
            last_updated   TEXT,
            meta_score     REAL DEFAULT 0,
            transition     TEXT DEFAULT '',
            PRIMARY KEY (chain, token_address)
        )
    """)

    for r in results:
        existing = conn.execute(
            "SELECT current_stage, stage_since FROM token_lifecycle WHERE chain=? AND token_address=?",
            (r.chain, r.token_address)
        ).fetchone()

        if existing:
            prev = existing["current_stage"]
            since = existing["stage_since"] if r.stage == prev else scan_time
            # 检测关键跃迁
            transition = ""
            if prev in ("ACCUMULATING", "CONTROLLED") and r.stage == "DISTRIBUTING":
                transition = f"⚡ {prev}→DISTRIBUTING"
                logger.warning(f"生命周期跃迁: {r.token_symbol} {transition}")
            conn.execute("""
                UPDATE token_lifecycle
                SET current_stage=?, prev_stage=?, stage_since=?, last_updated=?,
                    meta_score=?, transition=?, token_symbol=?
                WHERE chain=? AND token_address=?
            """, (r.stage, prev, since, scan_time, r.meta_score, transition,
                  r.token_symbol, r.chain, r.token_address))
        else:
            conn.execute("""
                INSERT INTO token_lifecycle
                (chain, token_address, token_symbol, current_stage, prev_stage,
                 stage_since, last_updated, meta_score)
                VALUES (?,?,?,?,?,?,?,?)
            """, (r.chain, r.token_address, r.token_symbol, r.stage, "",
                  scan_time, scan_time, r.meta_score))

    conn.commit()
    logger.info(f"生命周期表已更新 {len(results)} 条记录")


if __name__ == "__main__":
    main()
