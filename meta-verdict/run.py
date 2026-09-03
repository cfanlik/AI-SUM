#!/usr/bin/env python3
"""
meta-verdict 主入口
五引擎仲裁 → 趋势分析 → 综合排名 + 生命周期状态

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
from collector import get_connection, ensure_tables, collect_all_tokens, save_meta_result, ensure_hop2_tracking_table, collect_hop2_tracking
from arbitrator import run_arbitration, arbitrate
from trend_analyzer import analyze_trend
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
    ensure_hop2_tracking_table(conn)

    # ── 引擎健康自检 ──
    health = _check_engine_health(conn)

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

    # ── hop2 前置动态跟踪采集 ──
    tokens_to_track = [
        {"chain": d.chain, "token_address": d.token_address.lower(), "token_symbol": d.token_symbol}
        for d in all_data
    ]
    hop2_saved = collect_hop2_tracking(conn, scan_time, tokens_to_track)
    logger.info(f"hop2_tracking 动态采集完成: {hop2_saved} 条")

    # 从刚灌好的 hop2_tracking 表中批量读取当前快照百分比
    hop2_map = {}
    try:
        for row in conn.execute("SELECT token_address, hop2_acc_pct FROM hop2_tracking WHERE scan_time = ?", [scan_time]):
            hop2_map[row["token_address"].lower()] = row["hop2_acc_pct"] or 0
    except Exception as _e:
        logger.error(f"加载 hop2 映射失败: {_e}")

    # 一次性完全体仲裁（出厂即获得 hop2 加分与最终裁决判定）
    all_arbitrated = [arbitrate(d, hop2_pct=hop2_map.get(d.token_address.lower(), 0.0), conn=conn, scan_time=scan_time) for d in all_data]

    # 重新分流生成最终排行列表
    acc_list = sorted([r for r in all_arbitrated if r.meta_verdict == "ACC"], key=lambda r: r.meta_score, reverse=True)
    dist_list = sorted([r for r in all_arbitrated if r.meta_verdict == "DIST"], key=lambda r: r.meta_score)
    all_results = acc_list + dist_list

    # 一次性保存全部完全体代币结果入库
    for r in all_arbitrated:
        save_meta_result(conn, {
            "scan_time":      scan_time,
            "chain":          r.chain,
            "token_address":  r.token_address,
            "token_symbol":   r.token_symbol,
            "meta_score":     r.meta_score,
            "meta_score_smooth": r.meta_score_smooth,
            "meta_verdict":   r.meta_verdict,
            "engine_hits":    r.engine_hits,
            "master_signal":  r.master_signal,
            "opus_verdict":   r.opus_verdict,
            "unified_signal": r.unified_signal,
            "whale_level":    r.whale_level,
            "cb_verdict":     r.cb_verdict,
            "stage":          r.stage,
            "confidence_tier": getattr(r, "confidence_tier", "L3-Watch"),
            "resilience_index": getattr(r, "resilience_index", 0.0),
            "resilience_norm": getattr(r, "resilience_norm", 0.5),
            "master_score":   r.master_score,
            "opus_score":     r.opus_score,
            "unified_score":  r.unified_score,
            "whale_score":    r.whale_score,
            "cb_score":       r.cb_score,
            "hop2_score":     r.hop2_score,
        })

    # ── 矛盾检测 ──
    from conflict_detector import detect_conflicts
    conflicts = detect_conflicts(all_arbitrated, all_data)

    # ── 趋势分析（对比上一轮，基于完全体数据）──
    trend = analyze_trend(conn, all_results, scan_time)
    if trend.has_prev:
        logger.info(f"趋势对比: vs {trend.prev_scan_time} | "
                     f"新进{len(trend.newcomers)} 退出{len(trend.exits)} "
                     f"↑{trend.score_up} ↓{trend.score_down} →{trend.stable} "
                     f"跃变{len(trend.jumps)}")

    # 更新生命周期表 (完美追踪完全体状态)
    _update_lifecycle(conn, all_arbitrated, scan_time)

    # 生成报告（含趋势+健康+矛盾）
    generate_report(acc_list, dist_list, len(all_data), scan_time, trend, health, conflicts, all_arbitrated=all_arbitrated)

    # ── 拉升前兆扫描 ──
    try:
        from pump_detector import run as pump_run
        pump_results = pump_run(scan_time)
        imm_count = sum(1 for r in pump_results if r.get("level") == "IMMINENT")
        logger.info(f"pump_detector 完成: {len(pump_results)} 代币, IMMINENT={imm_count}")
    except Exception as e:
        logger.error(f"pump_detector 失败: {e}")
    conn.close()


def _check_engine_health(conn) -> list[dict]:
    """检测每个引擎最新 scan_time，若 > 阈值 → 告警"""
    checks = {
        "master":     ("watchlist",              "last_updated"),
        "opus":       ("opus_snapshots",         "scan_time"),
        "whale":      ("whale_snapshots",        "scan_time"),
        "cost-basis": ("cost_basis_snapshots",   "scan_time"),
        "unified":    ("unified_results",        "scan_time"),
    }
    results = []
    now = datetime.now()
    for name, (table, col) in checks.items():
        try:
            row = conn.execute(f"SELECT MAX({col}) FROM {table}").fetchone()
            if not row or not row[0]:
                results.append({"engine": name, "status": "❌", "detail": "表为空", "gap_h": 999})
                continue
            last_str = row[0]
            # 兼容多种日期格式（含时区）
            last = None
            # 先尝试 fromisoformat（Python 3.11+ 支持时区）
            try:
                last = datetime.fromisoformat(last_str.replace("+00:00", "").replace("Z", ""))
            except Exception:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        last = datetime.strptime(last_str, fmt)
                        break
                    except ValueError:
                        continue
            if last is None:
                results.append({"engine": name, "status": "⚠", "detail": f"日期解析失败: {last_str}", "gap_h": 999})
                continue
            gap_h = (now - last).total_seconds() / 3600
            if gap_h < config.ENGINE_HEALTH_MAX_HOURS:
                results.append({"engine": name, "status": "✅", "detail": f"{gap_h:.1f}h前", "gap_h": gap_h})
            else:
                results.append({"engine": name, "status": "⚠", "detail": f"{gap_h:.0f}h前", "gap_h": gap_h})
        except Exception as e:
            results.append({"engine": name, "status": "❌", "detail": str(e), "gap_h": 999})
    return results


def _update_lifecycle(conn, results, scan_time: str):
    """更新代币生命周期状态机"""
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
