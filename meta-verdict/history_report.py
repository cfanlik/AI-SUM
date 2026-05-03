#!/usr/bin/env python3
"""
AI-SUM 长期分析报告
4 模块融合: 信号回测 + holder迁移 + 积分时序 + 信号×收益
只读 select.db，可写 select-sum.db
输出: /opt/AI-SUM/report/history/history_YYYYMMDD.md
"""
import sqlite3
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

SRC_DB = "/opt/select-coin/data/select.db"
SUM_DB = "/opt/AI-SUM/select-sum.db"
REPORT_DIR = "/opt/AI-SUM/report/history"


def connect(path, readonly=False):
    uri = f"file:{path}?mode=ro" if readonly else path
    c = sqlite3.connect(uri, uri=readonly)
    c.row_factory = sqlite3.Row
    return c


# ══════════════════════════════════════════════════════════════
# 模块 1: 信号回测
# ══════════════════════════════════════════════════════════════
def backtest_watchlist(src, sumdb):
    """watchlist DIAMOND/RED/YELLOW 信号后 7d/14d/至今 收益"""
    tokens = sumdb.execute("""
        SELECT token_address, token_symbol, signal_level, last_updated
        FROM watchlist WHERE signal_level IN ('DIAMOND','RED','YELLOW')
    """).fetchall()

    results = []
    for t in tokens:
        addr = t["token_address"]
        sig_date = t["last_updated"][:19].replace("T", " ").split("+")[0]
        try:
            sd = datetime.strptime(sig_date[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue

        # 入场价
        p0 = src.execute(
            "SELECT price_usd FROM gecko_market_data "
            "WHERE token_address=? AND scan_time>=? AND price_usd>0 "
            "ORDER BY scan_time LIMIT 1", (addr, sig_date)
        ).fetchone()
        if not p0 or p0[0] <= 0:
            continue
        entry = p0[0]

        # 7d 后价格
        d7 = (sd + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        p7 = src.execute(
            "SELECT price_usd FROM gecko_market_data "
            "WHERE token_address=? AND scan_time>=? AND price_usd>0 "
            "ORDER BY scan_time LIMIT 1", (addr, d7)
        ).fetchone()

        # 14d 后价格
        d14 = (sd + timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
        p14 = src.execute(
            "SELECT price_usd FROM gecko_market_data "
            "WHERE token_address=? AND scan_time>=? AND price_usd>0 "
            "ORDER BY scan_time LIMIT 1", (addr, d14)
        ).fetchone()

        # 最新价格
        pnow = src.execute(
            "SELECT price_usd FROM gecko_market_data "
            "WHERE token_address=? AND price_usd>0 "
            "ORDER BY scan_time DESC LIMIT 1", (addr,)
        ).fetchone()

        r = {
            "symbol": t["token_symbol"], "signal": t["signal_level"],
            "date": sig_date[:10], "entry": entry,
            "ret_7d": ((p7[0] - entry) / entry * 100) if p7 else None,
            "ret_14d": ((p14[0] - entry) / entry * 100) if p14 else None,
            "ret_now": ((pnow[0] - entry) / entry * 100) if pnow else None,
            "price_now": pnow[0] if pnow else 0,
        }
        results.append(r)

    # 汇总
    lines = ["## 📊 信号回测（watchlist 7d/14d 收益验证）", ""]

    # 按信号分组统计
    lines += ["### 汇总", "",
              "| 信号 | 样本 | 7d胜率 | 7d均收益 | 14d胜率 | 14d均收益 | 至今胜率 | 至今均收益 |",
              "|------|------|--------|---------|---------|----------|---------|----------|"]
    for sig in ["DIAMOND", "RED", "YELLOW"]:
        grp = [r for r in results if r["signal"] == sig]
        if not grp:
            continue
        r7 = [r["ret_7d"] for r in grp if r["ret_7d"] is not None]
        r14 = [r["ret_14d"] for r in grp if r["ret_14d"] is not None]
        rnow = [r["ret_now"] for r in grp if r["ret_now"] is not None]
        def wr(lst): return f"{sum(1 for x in lst if x>0)/len(lst)*100:.0f}%" if lst else "—"
        def avg(lst): return f"{sum(lst)/len(lst):+.1f}%" if lst else "—"
        lines.append(f"| {sig} | {len(grp)} | {wr(r7)} | {avg(r7)} | {wr(r14)} | {avg(r14)} | {wr(rnow)} | {avg(rnow)} |")

    # 明细(DIAMOND+RED)
    detail = [r for r in results if r["signal"] in ("DIAMOND", "RED")]
    detail.sort(key=lambda r: r["ret_now"] or 0, reverse=True)
    if detail:
        lines += ["", "### 明细（DIAMOND + RED）", "",
                  "| 代币 | 信号 | 信号日 | 入场价 | 7d收益 | 14d收益 | 当前收益 |",
                  "|------|------|--------|--------|--------|---------|---------|"]
        for r in detail[:20]:
            def fmt(v): return f"{v:+.1f}%" if v is not None else "—"
            sig_emoji = "💎" if r["signal"] == "DIAMOND" else "🔴"
            lines.append(f"| {r['symbol']} | {sig_emoji} | {r['date']} | ${r['entry']:.4f} | {fmt(r['ret_7d'])} | {fmt(r['ret_14d'])} | {fmt(r['ret_now'])} |")

    lines.append("")
    return "\n".join(lines), results


# ══════════════════════════════════════════════════════════════
# 模块 2: Holder 迁移分析
# ══════════════════════════════════════════════════════════════
def migration_analysis(src, sumdb):
    """bubblemap 7d/14d holder 变动"""
    now = datetime.now()
    d7 = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    d14 = (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")

    # 获取所有 token
    tokens = src.execute(
        "SELECT DISTINCT token_address FROM bubblemap_holders"
    ).fetchall()

    # symbol 映射
    sym_map = {}
    for r in sumdb.execute("SELECT token_address, token_symbol FROM watchlist"):
        sym_map[r[0]] = r[1]

    results = []
    for t in tokens:
        addr = t[0]
        sym = sym_map.get(addr, "")

        # 最新快照时间
        latest = src.execute(
            "SELECT MAX(snapshot_time) FROM bubblemap_holders WHERE token_address=?", (addr,)
        ).fetchone()[0]
        if not latest:
            continue

        # 7d前最近快照
        old7 = src.execute(
            "SELECT MAX(snapshot_time) FROM bubblemap_holders WHERE token_address=? AND snapshot_time<=?",
            (addr, d7)
        ).fetchone()[0]

        if not old7:
            continue

        # 最新 Top50
        new_rows = src.execute(
            "SELECT wallet_address, rank, hold_percentage, is_accumulating "
            "FROM bubblemap_holders WHERE token_address=? AND snapshot_time=? "
            "AND rank<=50 AND is_cex=0 AND is_contract=0",
            (addr, latest)
        ).fetchall()

        # 旧 Top50
        old_rows = src.execute(
            "SELECT wallet_address, rank, hold_percentage, is_accumulating "
            "FROM bubblemap_holders WHERE token_address=? AND snapshot_time=? "
            "AND rank<=50 AND is_cex=0 AND is_contract=0",
            (addr, old7)
        ).fetchall()

        if not new_rows or not old_rows:
            continue

        new_set = {r["wallet_address"] for r in new_rows}
        old_set = {r["wallet_address"] for r in old_rows}
        retained = new_set & old_set
        entered = new_set - old_set
        exited = old_set - new_set

        # Top10 集中度
        new_top10 = sum(r["hold_percentage"] for r in new_rows if r["rank"] <= 10)
        old_top10 = sum(r["hold_percentage"] for r in old_rows if r["rank"] <= 10)

        # 吸筹数
        new_acc = sum(1 for r in new_rows if r["is_accumulating"])
        old_acc = sum(1 for r in old_rows if r["is_accumulating"])

        retention = len(retained) / max(len(old_set), 1) * 100

        results.append({
            "symbol": sym, "addr": addr,
            "retention": retention,
            "entered": len(entered), "exited": len(exited),
            "top10_now": new_top10, "top10_delta": new_top10 - old_top10,
            "acc_now": new_acc, "acc_delta": new_acc - old_acc,
            "old_snap": old7[:10], "new_snap": latest[:10],
        })

    results.sort(key=lambda r: r["retention"])

    lines = ["## 🔄 Holder 迁移矩阵（7d 对比）", ""]

    # 只显示有 meta 信号的代币
    meta_syms = set()
    for r in sumdb.execute("SELECT DISTINCT token_symbol FROM meta_snapshots WHERE meta_verdict='ACC'"):
        meta_syms.add(r[0])

    acc_results = [r for r in results if r["symbol"] in meta_syms]
    acc_results.sort(key=lambda r: r["retention"], reverse=True)

    if acc_results:
        lines += ["### 吸筹代币 holder 变动", "",
                  "| 代币 | Top50留存% | 新进 | 退出 | Top10集中% | Δ集中 | 吸筹数Δ | 评级 |",
                  "|------|-----------|------|------|-----------|-------|---------|------|"]
        for r in acc_results[:25]:
            if r["retention"] >= 90:
                grade = "🔒极稳"
            elif r["retention"] >= 75:
                grade = "✅稳定"
            elif r["retention"] >= 60:
                grade = "⚠波动"
            else:
                grade = "❌流失"
            lines.append(
                f"| {r['symbol']} | {r['retention']:.0f}% | {r['entered']} | {r['exited']} "
                f"| {r['top10_now']:.1f}% | {r['top10_delta']:+.1f}% "
                f"| {r['acc_delta']:+d} | {grade} |"
            )

    # 异常流动
    danger = [r for r in results if r["retention"] < 60 and r["symbol"]]
    if danger:
        lines += ["", "### ⚠ 异常流动（Top50 留存 < 60%）", "",
                  "| 代币 | 留存% | 退出数 | Top10Δ | 含义 |",
                  "|------|-------|--------|--------|------|"]
        for r in danger[:10]:
            meaning = "大规模换手" if r["top10_delta"] < -5 else "底部换手"
            lines.append(f"| {r['symbol']} | {r['retention']:.0f}% | {r['exited']} | {r['top10_delta']:+.1f}% | {meaning} |")

    lines.append("")
    return "\n".join(lines), results


# ══════════════════════════════════════════════════════════════
# 模块 3: 积分时序分析
# ══════════════════════════════════════════════════════════════
def score_timeseries(sumdb):
    """meta_snapshots 积分斜率/波动/持续性"""
    rows = sumdb.execute("""
        SELECT token_symbol, scan_time, meta_score, meta_verdict, engine_hits,
               master_score, opus_score, unified_score, whale_score, cb_score
        FROM meta_snapshots ORDER BY token_symbol, scan_time
    """).fetchall()

    grouped = defaultdict(list)
    for r in rows:
        grouped[r["token_symbol"]].append(r)

    results = []
    for sym, snapshots in grouped.items():
        scores = [s["meta_score"] for s in snapshots]
        if len(scores) < 3:
            continue

        # 斜率
        early = statistics.mean(scores[:3])
        recent = statistics.mean(scores[-3:])
        slope = (recent - early) / max(len(scores), 1)

        # 波动
        sigma = statistics.stdev(scores) if len(scores) >= 2 else 0

        # 连续 ACC 轮次（从末尾数）
        consec = 0
        for s in reversed(snapshots):
            if s["meta_verdict"] == "ACC":
                consec += 1
            else:
                break

        # 引擎稳定性
        hits = [s["engine_hits"] for s in snapshots]
        engine_stable = "稳定" if (max(hits) - min(hits)) <= 1 else "波动"

        # 方向标签
        if slope > 0.1:
            direction = "↑↑增强"
        elif slope > 0.02:
            direction = "↑微升"
        elif slope < -0.1:
            direction = "↓↓衰减"
        elif slope < -0.02:
            direction = "↓微降"
        else:
            direction = "→稳定"

        last5 = "→".join(f"{s:.1f}" for s in scores[-5:])

        results.append({
            "symbol": sym, "score": scores[-1], "slope": slope,
            "direction": direction, "sigma": sigma,
            "consec_acc": consec, "engine_stable": engine_stable,
            "rounds": len(scores), "last5": last5,
            "peak": max(scores), "trough": min(scores),
            "verdict": snapshots[-1]["meta_verdict"],
        })

    lines = ["## 📈 积分时序分析", ""]

    # 信号增强
    rising = sorted([r for r in results if r["slope"] > 0.02 and r["verdict"] == "ACC"],
                    key=lambda r: r["slope"], reverse=True)
    if rising:
        lines += ["### 信号增强 Top 10", "",
                  "| 代币 | 当前分 | 斜率 | 方向 | σ | 连续ACC | 引擎 | 序列(最近5轮) |",
                  "|------|--------|------|------|---|---------|------|-------------|"]
        for r in rising[:10]:
            lines.append(
                f"| {r['symbol']} | {r['score']:.1f} | {r['slope']:+.2f} "
                f"| {r['direction']} | {r['sigma']:.1f} | {r['consec_acc']} "
                f"| {r['engine_stable']} | {r['last5']} |"
            )

    # 信号衰减
    falling = sorted([r for r in results if r["slope"] < -0.02],
                     key=lambda r: r["slope"])
    if falling:
        lines += ["", "### 信号衰减 Top 5", "",
                  "| 代币 | 当前分 | 斜率 | 峰值 | 衰减幅 | 序列 |",
                  "|------|--------|------|------|--------|------|"]
        for r in falling[:5]:
            decay = (r["score"] - r["peak"]) / max(abs(r["peak"]), 0.1) * 100
            lines.append(
                f"| {r['symbol']} | {r['score']:.1f} | {r['slope']:+.2f} "
                f"| {r['peak']:.1f} | {decay:.0f}% | {r['last5']} |"
            )

    # 高波动
    volatile = sorted([r for r in results if r["sigma"] > 1.0],
                      key=lambda r: r["sigma"], reverse=True)
    if volatile:
        lines += ["", "### 高波动代币（σ > 1.0）", "",
                  "| 代币 | 当前分 | σ | 波幅 | 判定 | 序列 |",
                  "|------|--------|---|------|------|------|"]
        for r in volatile[:10]:
            amp = r["peak"] - r["trough"]
            lines.append(
                f"| {r['symbol']} | {r['score']:.1f} | {r['sigma']:.1f} "
                f"| {amp:.1f} | {r['verdict']} | {r['last5']} |"
            )

    lines.append("")
    return "\n".join(lines), results


# ══════════════════════════════════════════════════════════════
# 模块 4: 信号×收益分布
# ══════════════════════════════════════════════════════════════
def signal_price_corr(backtest_results, sumdb):
    """按积分区间和引擎数分桶统计胜率"""
    # 获取每个代币的 meta_score
    meta_map = {}
    for r in sumdb.execute("""
        SELECT token_symbol, meta_score, engine_hits
        FROM meta_snapshots WHERE scan_time=(SELECT MAX(scan_time) FROM meta_snapshots)
    """):
        meta_map[r["token_symbol"]] = {"score": r["meta_score"], "engines": r["engine_hits"]}

    # 合并
    merged = []
    for bt in backtest_results:
        m = meta_map.get(bt["symbol"])
        if m:
            bt["meta_score"] = m["score"]
            bt["engines"] = m["engines"]
            merged.append(bt)

    lines = ["## 📉 信号强度 × 收益分布", ""]

    # 按积分分桶
    buckets = [
        ("≥7 (极强)", lambda r: r["meta_score"] >= 7),
        ("5-7 (强)", lambda r: 5 <= r["meta_score"] < 7),
        ("3-5 (中)", lambda r: 3 <= r["meta_score"] < 5),
        ("0-3 (弱)", lambda r: 0 <= r["meta_score"] < 3),
        ("<0 (负)", lambda r: r["meta_score"] < 0),
    ]
    lines += ["### 按综合分分桶", "",
              "| 积分区间 | 样本 | 7d胜率 | 7d均收益 | 至今胜率 | 至今均收益 |",
              "|----------|------|--------|---------|---------|----------|"]
    for name, pred in buckets:
        grp = [r for r in merged if pred(r)]
        if not grp:
            continue
        r7 = [r["ret_7d"] for r in grp if r["ret_7d"] is not None]
        rnow = [r["ret_now"] for r in grp if r["ret_now"] is not None]
        def wr(lst):
            return f"{sum(1 for x in lst if x > 0) / len(lst) * 100:.0f}%" if lst else "—"
        def avg(lst):
            return f"{sum(lst) / len(lst):+.1f}%" if lst else "—"
        lines.append(f"| {name} | {len(grp)} | {wr(r7)} | {avg(r7)} | {wr(rnow)} | {avg(rnow)} |")

    # 按引擎数分桶
    eng_buckets = [("≥4", 4, 99), ("3", 3, 3), ("2", 2, 2), ("1", 1, 1)]
    lines += ["", "### 按引擎数分桶", "",
              "| 引擎数 | 样本 | 7d胜率 | 7d均收益 | 至今胜率 | 至今均收益 |",
              "|--------|------|--------|---------|---------|----------|"]
    for name, lo, hi in eng_buckets:
        grp = [r for r in merged if lo <= r.get("engines", 0) <= hi]
        if not grp:
            continue
        r7 = [r["ret_7d"] for r in grp if r["ret_7d"] is not None]
        rnow = [r["ret_now"] for r in grp if r["ret_now"] is not None]
        def wr(lst):
            return f"{sum(1 for x in lst if x > 0) / len(lst) * 100:.0f}%" if lst else "—"
        def avg(lst):
            return f"{sum(lst) / len(lst):+.1f}%" if lst else "—"
        lines.append(f"| {name} | {len(grp)} | {wr(r7)} | {avg(r7)} | {wr(rnow)} | {avg(rnow)} |")

    lines.append("")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════
def main():
    import time
    t0 = time.time()
    print(f"AI-SUM 长期分析报告 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    src = connect(SRC_DB, readonly=True)
    sumdb = connect(SUM_DB)

    # 模块 1
    print("  [1/4] 信号回测...")
    bt_md, bt_data = backtest_watchlist(src, sumdb)
    print(f"        {len(bt_data)} 个代币有回测数据")

    # 模块 2
    print("  [2/4] holder 迁移...")
    mig_md, mig_data = migration_analysis(src, sumdb)
    print(f"        {len(mig_data)} 个代币有迁移数据")

    # 模块 3
    print("  [3/4] 积分时序...")
    ts_md, ts_data = score_timeseries(sumdb)
    print(f"        {len(ts_data)} 个代币有时序数据")

    # 模块 4
    print("  [4/4] 信号×收益...")
    corr_md = signal_price_corr(bt_data, sumdb)

    # 组装报告
    today = datetime.now().strftime("%Y-%m-%d")
    header = f"""# 📊 AI-SUM 长期分析报告 — {today}

> 数据源: bubblemap 31天×197代币 + gecko 23天 + meta {len(ts_data)}代币
> 生成: history_report.py | 耗时: {{elapsed:.1f}}s

---

"""
    body = "\n---\n\n".join([bt_md, mig_md, ts_md, corr_md])
    elapsed = time.time() - t0
    md = header.format(elapsed=elapsed) + body
    md += f"\n\n---\n*生成时间: {datetime.now().isoformat()} | 耗时: {elapsed:.1f}s*\n"

    # 写入
    out_dir = Path(REPORT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"history_{datetime.now().strftime('%Y%m%d')}.md"
    path.write_text(md, encoding="utf-8")
    print(f"\n📄 报告: {path} ({len(md)} bytes, {elapsed:.1f}s)")

    src.close()
    sumdb.close()


if __name__ == "__main__":
    main()
