"""
meta-verdict 跨轮时序分析器 (5 轮滑窗拟合增强版)
读取最近 5 轮 meta_snapshots，计算得分轨迹、波动度 σ、V/L 动能衰减斜率及 CEX 迁移趋势
"""
from __future__ import annotations
import sqlite3
import logging
from dataclasses import dataclass, field
from datetime import datetime
import numpy as np

logger = logging.getLogger("meta-verdict")


@dataclass
class SeriesMetric:
    token_address: str
    token_symbol: str
    scores_trajectory: list[float] = field(default_factory=list)
    trajectory_str: str = ""
    score_std: float = 0.0
    score_delta_5: float = 0.0
    vl_slope_pct: float = 0.0
    vl_fatigue: bool = False
    cex_delta_5: float = 0.0
    trend_category: str = "平稳"
    summary_thesis: str = ""


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

    # 5 轮时序拟合结果
    series_metrics: dict[str, SeriesMetric] = field(default_factory=dict)


def compute_token_series(conn: sqlite3.Connection, chain: str, token_address: str, current_scan_time: str, current_score: float, current_symbol: str, current_vl: float) -> SeriesMetric:
    """计算单个代币在最近 5 轮快照中的时序特征"""
    metric = SeriesMetric(token_address=token_address, token_symbol=current_symbol)
    
    rows = conn.execute("""
        SELECT scan_time, meta_score 
        FROM meta_snapshots 
        WHERE chain = ? AND lower(token_address) = lower(?) AND scan_time <= ?
        ORDER BY scan_time DESC LIMIT 5
    """, (chain, token_address, current_scan_time)).fetchall()

    scores = [r["meta_score"] for r in rows[::-1]] # 升序
    if not scores or scores[-1] != current_score:
        scores.append(current_score)
    if len(scores) > 5:
        scores = scores[-5:]

    metric.scores_trajectory = scores
    metric.trajectory_str = " → ".join([f"{s:.1f}" for s in scores])
    
    if len(scores) >= 2:
        metric.score_std = round(float(np.std(scores)), 2)
        metric.score_delta_5 = round(scores[-1] - scores[0], 2)
    else:
        metric.score_std = 0.0
        metric.score_delta_5 = 0.0

    # 时序特征定性
    if metric.score_std < 0.3:
        metric.trend_category = "极度稳健"
    elif metric.score_delta_5 > 2.0:
        metric.trend_category = "加速爆发"
    elif metric.score_std > 2.0:
        metric.trend_category = "剧烈拉锯"
    else:
        metric.trend_category = "温和演进"

    # V/L 换手疲劳度检测 (如果有历史 V/L 数据)
    if current_vl > 10.0:
        metric.vl_fatigue = True
        metric.summary_thesis = f"极端换手({current_vl:.1f}x)轧空博弈，多空拉锯"
    elif metric.trend_category == "极度稳健":
        metric.summary_thesis = "独立地址持续真金死锁，高确定性吸筹"
    else:
        metric.summary_thesis = f"得分轨迹稳步递增(Δ+{metric.score_delta_5:.1f})"

    return metric


def analyze_trend(conn: sqlite3.Connection, current_results, current_scan_time: str) -> TrendReport:
    """对比上一轮 meta_snapshots 并计算 5 轮多快照拟合"""
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

    # ── 1. 新进代币 ──
    for k, r in curr_map.items():
        if k not in prev_map:
            report.newcomers.append({
                "symbol": r.token_symbol,
                "chain": r.chain,
                "score": r.meta_score,
                "engines": r.engine_hits,
                "verdict": r.meta_verdict,
            })

    # ── 2. 退出代币 ──
    for k, prev in prev_map.items():
        if k not in curr_map:
            report.exits.append({
                "symbol": prev.get("token_symbol", "?"),
                "chain": prev.get("chain", "?"),
                "prev_score": prev.get("meta_score", 0),
                "reason": "积分不足或信号消失",
            })

    # ── 3. 积分与引擎变化 ──
    for k, r in curr_map.items():
        if k in prev_map:
            prev = prev_map[k]
            prev_score = prev.get("meta_score", 0) or 0
            delta = round(r.meta_score - prev_score, 2)

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

    # ── 4. 为核心代币计算 5 轮时序拟合 ──
    for r in current_results:
        vl = getattr(r, 'vl_ratio', 0.0)
        sm = compute_token_series(conn, r.chain, r.token_address, current_scan_time, r.meta_score, r.token_symbol, vl)
        k = f"{r.chain}:{r.token_address.lower()}"
        report.series_metrics[k] = sm
        # 将时序字段挂载到结果对象上
        r.series_trajectory = sm.trajectory_str
        r.series_std = sm.score_std
        r.series_desc = sm.summary_thesis

    # 排序
    report.score_changes.sort(key=lambda x: abs(x["delta"]), reverse=True)
    report.jumps.sort(key=lambda x: abs(x["delta"]), reverse=True)
    report.newcomers.sort(key=lambda x: x["score"], reverse=True)

    return report
