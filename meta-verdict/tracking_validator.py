#!/usr/bin/env python3
"""
tracking_validator.py — 方法论跟踪验证
对比 token_history 多日数据，验证 methodology.md 中 H1-H4 假设
用法: python3 tracking_validator.py [--baseline 2026-05-06] [--current 2026-05-10]
输出: report/history/tracking_YYYYMMDD.md
"""
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path
import argparse

SUM_DB = "/opt/AI-SUM/select-sum.db"
REPORT_DIR = "/opt/AI-SUM/report/history"


def connect():
    c = sqlite3.connect(SUM_DB)
    c.row_factory = sqlite3.Row
    return c


def get_history(db, date):
    rows = db.execute(
        "SELECT * FROM token_history WHERE computed_date=?", (date,)
    ).fetchall()
    return {r["token_symbol"]: dict(r) for r in rows}


def get_latest_meta(db):
    rows = db.execute(
        "SELECT token_symbol, meta_score, meta_verdict, engine_hits, stage "
        "FROM meta_snapshots WHERE scan_time=(SELECT MAX(scan_time) FROM meta_snapshots)"
    ).fetchall()
    return {r["token_symbol"]: dict(r) for r in rows}


def validate(baseline_date, current_date):
    db = connect()
    base = get_history(db, baseline_date)
    curr = get_history(db, current_date)
    meta = get_latest_meta(db)

    lines = [
        f"# 📊 方法论跟踪验证 — {current_date}",
        f"",
        f"> 基线: {baseline_date} | 对比: {current_date}",
        f"> 基线样本: {len(base)} | 对比样本: {len(curr)}",
        "",
        "---",
    ]

    # ── H1: 低留存+高收益代币后续走势 ──
    lines += ["", "## H1: 低留存+高收益代币后续走势", ""]
    h1_tokens = []
    for sym in sorted(set(base) & set(curr)):
        b, c = base[sym], curr[sym]
        b_ret = b.get("price_now_ret") or 0
        c_ret = c.get("price_now_ret") or 0
        b_ret7d = b.get("retention_7d") or 100
        if b_ret > 50 and b_ret7d < 50:  # 基线: 高收益+低留存
            delta = c_ret - b_ret
            h1_tokens.append((sym, b_ret, c_ret, delta, b_ret7d))

    if h1_tokens:
        lines += [
            "| 代币 | 基线收益 | 当前收益 | Δ | 基线留存 | 结论 |",
            "|------|---------|---------|---|---------|------|",
        ]
        for sym, b_ret, c_ret, delta, ret7d in h1_tokens:
            if delta < -30:
                conclusion = "❌ 回落 → 遗漏检测有效"
            elif delta > 0:
                conclusion = "✅ 继续涨 → 换手非出货"
            else:
                conclusion = "⚠ 小幅波动"
            lines.append(f"| {sym} | {b_ret:+.1f}% | {c_ret:+.1f}% | {delta:+.1f}pp | {ret7d:.0f}% | {conclusion} |")
    else:
        lines.append("无符合条件的代币（高收益+低留存）")

    # ── H2: 3引擎 vs 4引擎 ──
    lines += ["", "## H2: 3引擎组合 vs 4引擎", ""]
    engine_buckets = {}
    for sym, m in meta.items():
        hits = m.get("engine_hits", 0)
        c_data = curr.get(sym)
        if c_data and c_data.get("price_now_ret") is not None:
            bucket = min(hits, 4)
            engine_buckets.setdefault(bucket, []).append(c_data["price_now_ret"])

    if engine_buckets:
        lines += [
            "| 引擎数 | 样本 | 胜率 | 均收 | 中位 |",
            "|--------|------|------|------|------|",
        ]
        for k in sorted(engine_buckets):
            vals = engine_buckets[k]
            wr = sum(1 for v in vals if v > 0) / max(len(vals), 1) * 100
            avg = statistics.mean(vals) if vals else 0
            med = statistics.median(vals) if vals else 0
            label = f"≥{k}" if k == 4 else str(k)
            lines.append(f"| {label} | {len(vals)} | {wr:.0f}% | {avg:+.1f}% | {med:+.1f}% |")

    # ── H3: 积分分桶胜率 ──
    lines += ["", "## H3: 积分分桶胜率", ""]
    score_buckets = {"≥7": [], "5-7": [], "3-5": [], "0-3": [], "<0": []}
    for sym, m in meta.items():
        score = m.get("meta_score", 0)
        c_data = curr.get(sym)
        if c_data and c_data.get("price_now_ret") is not None:
            ret = c_data["price_now_ret"]
            if score >= 7:
                score_buckets["≥7"].append(ret)
            elif score >= 5:
                score_buckets["5-7"].append(ret)
            elif score >= 3:
                score_buckets["3-5"].append(ret)
            elif score >= 0:
                score_buckets["0-3"].append(ret)
            else:
                score_buckets["<0"].append(ret)

    lines += [
        "| 积分区间 | 样本 | 胜率 | 均收 | 中位 |",
        "|----------|------|------|------|------|",
    ]
    for bucket, vals in score_buckets.items():
        if vals:
            wr = sum(1 for v in vals if v > 0) / len(vals) * 100
            avg = statistics.mean(vals)
            med = statistics.median(vals)
            lines.append(f"| {bucket} | {len(vals)} | {wr:.0f}% | {avg:+.1f}% | {med:+.1f}% |")

    # ── H4: 2分代币胜率 ──
    lines += ["", "## H4: 2分代币胜率（Recall 提升评估）", ""]
    two_point = []
    for sym, m in meta.items():
        score = m.get("meta_score", 0)
        if 2 <= score < 3:
            c_data = curr.get(sym)
            if c_data and c_data.get("price_now_ret") is not None:
                two_point.append((sym, score, c_data["price_now_ret"]))

    if two_point:
        wr = sum(1 for _, _, r in two_point if r > 0) / len(two_point) * 100
        lines.append(f"2分代币: {len(two_point)}个, 胜率 {wr:.0f}%")
        lines += ["", "| 代币 | 积分 | 至今收益 |", "|------|------|---------|"]
        for sym, score, ret in sorted(two_point, key=lambda x: x[2], reverse=True)[:15]:
            lines.append(f"| {sym} | {score:.1f} | {ret:+.1f}% |")
        lines.append(f"")
        if wr > 45:
            lines.append("**结论**: 2分胜率 >45% → 降阈值有价值")
        else:
            lines.append("**结论**: 2分胜率 ≤45% → 维持当前阈值")
    else:
        lines.append("无 2分代币数据")

    # ── 收益大幅变动 ──
    lines += ["", "---", "", "## 📈 收益显著变动（Δ > 10pp）", ""]
    big_moves = []
    for sym in sorted(set(base) & set(curr)):
        b_ret = (base[sym].get("price_now_ret") or 0)
        c_ret = (curr[sym].get("price_now_ret") or 0)
        delta = c_ret - b_ret
        if abs(delta) > 10:
            big_moves.append((sym, b_ret, c_ret, delta))

    if big_moves:
        big_moves.sort(key=lambda x: x[3])
        lines += [
            "| 代币 | 基线收益 | 当前收益 | Δpp | 趋势 |",
            "|------|---------|---------|-----|------|",
        ]
        for sym, b_ret, c_ret, delta in big_moves:
            trend = "📈暴涨" if delta > 30 else "📉暴跌" if delta < -30 else "↑上升" if delta > 0 else "↓下跌"
            lines.append(f"| {sym} | {b_ret:+.1f}% | {c_ret:+.1f}% | {delta:+.1f} | {trend} |")

    # ── 方法论一致性 ──
    lines += ["", "---", "", "## ✅ 方法论一致性检查", ""]
    checks = []

    # 检查1: ACC 代币中留存<50%的比例
    acc_low_ret = 0
    acc_total = 0
    for sym, m in meta.items():
        if m.get("meta_verdict") == "ACC":
            acc_total += 1
            c_data = curr.get(sym)
            if c_data and (c_data.get("retention_7d") or 100) < 50:
                acc_low_ret += 1
    checks.append(f"ACC 代币中留存<50%: {acc_low_ret}/{acc_total} ({acc_low_ret/max(acc_total,1)*100:.0f}%)")

    # 检查2: DIST 代币数量
    dist_count = sum(1 for m in meta.values() if m.get("meta_verdict") == "DIST")
    checks.append(f"DIST 代币: {dist_count}")

    # 检查3: 信号覆盖率
    all_profitable = sum(1 for c in curr.values() if (c.get("price_now_ret") or 0) > 0)
    acc_profitable = sum(1 for sym, m in meta.items()
                        if m.get("meta_verdict") == "ACC"
                        and curr.get(sym) and (curr[sym].get("price_now_ret") or 0) > 0)
    recall = acc_profitable / max(all_profitable, 1) * 100
    checks.append(f"Recall: {acc_profitable}/{all_profitable} ({recall:.0f}%)")

    for c in checks:
        lines.append(f"- {c}")

    lines += ["", f"---", f"*生成时间: {datetime.now().isoformat()} | 基线: {baseline_date}*"]

    # 写入报告
    Path(REPORT_DIR).mkdir(parents=True, exist_ok=True)
    report_path = f"{REPORT_DIR}/tracking_{current_date.replace('-', '')}.md"
    Path(report_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已生成: {report_path}")
    print(f"假设验证: H1={len(h1_tokens)}代币, H2={len(engine_buckets)}桶, H3={len(score_buckets)}桶, H4={len(two_point)}代币")
    return report_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", default="2026-05-06", help="基线日期")
    parser.add_argument("--current", default=datetime.now().strftime("%Y-%m-%d"), help="当前日期")
    args = parser.parse_args()
    validate(args.baseline, args.current)
