"""
meta-verdict 跨轮趋势分析器
读取上一轮 meta_snapshots，计算 diff
"""
from __future__ import annotations
import sqlite3
import logging
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger("meta-verdict")


@dataclass
class TrendReport:
    prev_scan_time: str = ""
    has_prev: bool = False

    newcomers: list = field(default_factory=list)      # {symbol, chain, score, engines}
    exits: list = field(default_factory=list)           # {symbol, chain, prev_score, reason}
    score_changes: list = field(default_factory=list)   # {symbol, chain, prev, curr, delta, cause}
    engine_changes: list = field(default_factory=list)  # {symbol, engine, prev_signal, curr_signal}

    score_up: int = 0
    score_down: int = 0
    stable: int = 0
    jumps: list = field(default_factory=list)           # |delta| >= 1.5


def analyze_trend(conn: sqlite3.Connection, current_results, current_scan_time: str) -> TrendReport:
    """对比上一轮 meta_snapshots，计算全量 diff"""
    report = TrendReport()

    # 获取上一轮 scan_time
    row = conn.execute(
        "SELECT DISTINCT scan_time FROM meta_snapshots "
        "WHERE scan_time < ? ORDER BY scan_time DESC LIMIT 1",
        (current_scan_time,)
    ).fetchone()

    if not row:
        return report

    report.prev_scan_time = row[0]
    report.has_prev = True

    # 读取上一轮结果
    prev_rows = conn.execute(
        "SELECT * FROM meta_snapshots WHERE scan_time = ?",
        (report.prev_scan_time,)
    ).fetchall()

    prev_map = {}
    for r in prev_rows:
        k = f"{r['chain']}:{r['token_address'].lower()}"
        prev_map[k] = dict(r)

    curr_map = {}
    for r in current_results:
        k = f"{r.chain}:{r.token_address.lower()}"
        curr_map[k] = r

    # ── 新进代币 ──
    for k, r in curr_map.items():
        if k not in prev_map:
            report.newcomers.append({
                "symbol": r.token_symbol,
                "chain": r.chain,
                "score": r.meta_score,
                "engines": r.engine_hits,
                "verdict": r.meta_verdict,
            })

    # ── 退出代币 ──
    for k, prev in prev_map.items():
        if k not in curr_map:
            report.exits.append({
                "symbol": prev.get("token_symbol", "?"),
                "chain": prev.get("chain", "?"),
                "prev_score": prev.get("meta_score", 0),
                "reason": "积分不足或信号消失",
            })

    # ── 积分变化 ──
    for k, r in curr_map.items():
        if k in prev_map:
            prev = prev_map[k]
            prev_score = prev.get("meta_score", 0) or 0
            delta = round(r.meta_score - prev_score, 2)

            # 判断变化原因
            causes = []
            pm = prev.get("master_signal", "")
            if r.master_signal != pm:
                causes.append(f"master:{pm or '无'}→{r.master_signal or '无'}")
                report.engine_changes.append({
                    "symbol": r.token_symbol, "engine": "master",
                    "prev": pm, "curr": r.master_signal
                })

            po = prev.get("opus_verdict", "")
            if r.opus_verdict != po:
                causes.append(f"opus:{po or '无'}→{r.opus_verdict or '无'}")
                report.engine_changes.append({
                    "symbol": r.token_symbol, "engine": "opus",
                    "prev": po, "curr": r.opus_verdict
                })

            pw = prev.get("whale_level", "")
            if r.whale_level != pw:
                causes.append(f"whale:{pw or '无'}→{r.whale_level or '无'}")
                report.engine_changes.append({
                    "symbol": r.token_symbol, "engine": "whale",
                    "prev": pw, "curr": r.whale_level
                })

            pcb = prev.get("cb_verdict", "")
            if r.cb_verdict != pcb:
                causes.append(f"CB:{pcb or '无'}→{r.cb_verdict or '无'}")
                report.engine_changes.append({
                    "symbol": r.token_symbol, "engine": "cost-basis",
                    "prev": pcb, "curr": r.cb_verdict
                })

            pu = prev.get("unified_signal", "") if "unified_signal" in prev.keys() else ""
            if hasattr(r, 'unified_signal') and r.unified_signal != pu:
                causes.append(f"unified:{pu or '无'}→{r.unified_signal or '无'}")

            if abs(delta) >= 0.1:
                entry = {
                    "symbol": r.token_symbol,
                    "chain": r.chain,
                    "prev": prev_score,
                    "curr": r.meta_score,
                    "delta": delta,
                    "cause": ", ".join(causes) if causes else "积分微调",
                }
                report.score_changes.append(entry)

                if delta > 0:
                    report.score_up += 1
                else:
                    report.score_down += 1

                if abs(delta) >= 1.5:
                    report.jumps.append(entry)
            else:
                report.stable += 1

    # 排序
    report.score_changes.sort(key=lambda x: abs(x["delta"]), reverse=True)
    report.jumps.sort(key=lambda x: abs(x["delta"]), reverse=True)
    report.newcomers.sort(key=lambda x: x["score"], reverse=True)

    return report
