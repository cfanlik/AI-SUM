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
# 辅助: 查找信号首次出现时间（P-FIX 核心）
# ══════════════════════════════════════════════════════════════
def find_first_signal(sumdb, token_symbol, token_address):
    """从 meta_snapshots / unified_results 找信号首次出现时间，替代 watchlist.last_updated"""
    # 优先: meta_snapshots 中首次 ACC
    r = sumdb.execute(
        "SELECT MIN(scan_time) FROM meta_snapshots WHERE token_symbol=? AND meta_verdict='ACC'",
        (token_symbol,)
    ).fetchone()
    if r and r[0]:
        return r[0]

    # 次选: unified_results 中首次 DIAMOND/STRONG_ACC
    r = sumdb.execute(
        "SELECT MIN(scan_time) FROM unified_results WHERE token_address=? AND verdict IN ('DIAMOND','STRONG_ACC')",
        (token_address,)
    ).fetchone()
    if r and r[0]:
        return r[0]

    # 再次: unified_results 中任意非 NEUTRAL
    r = sumdb.execute(
        "SELECT MIN(scan_time) FROM unified_results WHERE token_address=? AND verdict != 'NEUTRAL'",
        (token_address,)
    ).fetchone()
    if r and r[0]:
        return r[0]

    return None


# ══════════════════════════════════════════════════════════════
# 模块 1: 信号回测（P-FIX: 使用首次信号时间）
# ══════════════════════════════════════════════════════════════
def backtest_watchlist(src, sumdb):
    """watchlist DIAMOND/RED/YELLOW 信号后 7d/14d/至今 收益"""
    tokens = sumdb.execute("""
        SELECT token_address, token_symbol, signal_level, last_updated
        FROM watchlist WHERE signal_level IN ('DIAMOND','RED','YELLOW')
    """).fetchall()

    today_str = datetime.now().strftime("%Y-%m-%d")
    results = []
    skipped_today = 0

    for t in tokens:
        addr = t["token_address"]
        sym = t["token_symbol"]

        # P-FIX: 用 find_first_signal 替代 last_updated
        first_seen = find_first_signal(sumdb, sym, addr)
        if not first_seen:
            # fallback 到 last_updated
            first_seen = t["last_updated"]

        sig_date = first_seen[:19].replace("T", " ").split("+")[0]
        try:
            sd = datetime.strptime(sig_date[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue

        # P-FIX: 过滤当天信号（收益=0%，无统计意义）
        if sig_date[:10] == today_str:
            skipped_today += 1
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

        days_held = (datetime.now() - sd).days

        r = {
            "symbol": sym, "signal": t["signal_level"],
            "date": sig_date[:10], "entry": entry,
            "ret_7d": ((p7[0] - entry) / entry * 100) if p7 else None,
            "ret_14d": ((p14[0] - entry) / entry * 100) if p14 else None,
            "ret_now": ((pnow[0] - entry) / entry * 100) if pnow else None,
            "price_now": pnow[0] if pnow else 0,
            "days_held": days_held,
            "addr": addr,
        }
        results.append(r)

    # 汇总
    lines = ["## 📊 信号回测（首次信号时间 × 收益验证）", ""]
    lines.append(f"> 时间基准: meta_snapshots/unified_results 首次信号时间 | 过滤当天信号 {skipped_today} 个")
    lines.append("")

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
                  "| 代币 | 信号 | 首次信号日 | 持有天数 | 入场价 | 7d收益 | 14d收益 | 当前收益 |",
                  "|------|------|----------|---------|--------|--------|---------|---------|"]
        for r in detail[:20]:
            def fmt(v): return f"{v:+.1f}%" if v is not None else "—"
            sig_emoji = "💎" if r["signal"] == "DIAMOND" else "🔴"
            lines.append(f"| {r['symbol']} | {sig_emoji} | {r['date']} | {r['days_held']}d | ${r['entry']:.4f} | {fmt(r['ret_7d'])} | {fmt(r['ret_14d'])} | {fmt(r['ret_now'])} |")

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

    tokens = src.execute(
        "SELECT DISTINCT token_address FROM bubblemap_holders"
    ).fetchall()

    sym_map = {}
    for r in sumdb.execute("SELECT token_address, token_symbol FROM watchlist"):
        sym_map[r[0]] = r[1]

    # meta 最新判定
    meta_verdict_map = {}
    try:
        for r in sumdb.execute(
            "SELECT token_symbol, meta_verdict FROM meta_snapshots "
            "WHERE scan_time=(SELECT MAX(scan_time) FROM meta_snapshots)"
        ):
            meta_verdict_map[r[0]] = r[1]
    except Exception:
        pass

    def _calc_retention(src, addr, latest, old_snap):
        """计算单个时间窗口的留存数据"""
        if not old_snap:
            return None
        new_rows = src.execute(
            "SELECT wallet_address, rank, hold_percentage, is_accumulating "
            "FROM bubblemap_holders WHERE token_address=? AND snapshot_time=? "
            "AND rank<=50 AND is_cex=0 AND is_contract=0",
            (addr, latest)
        ).fetchall()
        old_rows = src.execute(
            "SELECT wallet_address, rank, hold_percentage, is_accumulating "
            "FROM bubblemap_holders WHERE token_address=? AND snapshot_time=? "
            "AND rank<=50 AND is_cex=0 AND is_contract=0",
            (addr, old_snap)
        ).fetchall()
        if not new_rows or not old_rows:
            return None
        new_set = {r["wallet_address"] for r in new_rows}
        old_set = {r["wallet_address"] for r in old_rows}
        retained = new_set & old_set
        new_top10 = sum(r["hold_percentage"] for r in new_rows if r["rank"] <= 10)
        old_top10 = sum(r["hold_percentage"] for r in old_rows if r["rank"] <= 10)
        new_acc = sum(1 for r in new_rows if r["is_accumulating"])
        old_acc = sum(1 for r in old_rows if r["is_accumulating"])
        return {
            "retention": len(retained) / max(len(old_set), 1) * 100,
            "entered": len(new_set - old_set), "exited": len(old_set - new_set),
            "top10_now": new_top10, "top10_delta": new_top10 - old_top10,
            "acc_now": new_acc, "acc_delta": new_acc - old_acc,
        }

    results = []
    for t in tokens:
        addr = t[0]
        sym = sym_map.get(addr, "")

        latest = src.execute(
            "SELECT MAX(snapshot_time) FROM bubblemap_holders WHERE token_address=?", (addr,)
        ).fetchone()[0]
        if not latest:
            continue

        old7 = src.execute(
            "SELECT MAX(snapshot_time) FROM bubblemap_holders WHERE token_address=? AND snapshot_time<=?",
            (addr, d7)
        ).fetchone()[0]
        old14 = src.execute(
            "SELECT MAX(snapshot_time) FROM bubblemap_holders WHERE token_address=? AND snapshot_time<=?",
            (addr, d14)
        ).fetchone()[0]

        r7 = _calc_retention(src, addr, latest, old7)
        if not r7:
            continue

        r14 = _calc_retention(src, addr, latest, old14)

        row = {
            "symbol": sym, "addr": addr,
            "retention_7d": r7["retention"],
            "entered_7d": r7["entered"], "exited_7d": r7["exited"],
            "top10_now": r7["top10_now"], "top10_delta_7d": r7["top10_delta"],
            "acc_now": r7["acc_now"], "acc_delta_7d": r7["acc_delta"],
            "retention_14d": r14["retention"] if r14 else None,
            "top10_delta_14d": r14["top10_delta"] if r14 else None,
            "acc_delta_14d": r14["acc_delta"] if r14 else None,
            "old_snap": old7[:10], "new_snap": latest[:10],
            "meta_verdict": meta_verdict_map.get(sym, ""),
        }
        # 兼容旧字段名
        row["retention"] = row["retention_7d"]
        row["top10_delta"] = row["top10_delta_7d"]
        row["acc_delta"] = row["acc_delta_7d"]
        results.append(row)

    results.sort(key=lambda r: r["retention_7d"])

    lines = ["## 🔄 Holder 迁移矩阵（7d + 14d 对比）", ""]

    meta_syms = set()
    for r in sumdb.execute("SELECT DISTINCT token_symbol FROM meta_snapshots WHERE meta_verdict='ACC'"):
        meta_syms.add(r[0])

    acc_results = [r for r in results if r["symbol"] in meta_syms]
    acc_results.sort(key=lambda r: r["retention_7d"], reverse=True)

    if acc_results:
        lines += ["### 吸筹代币 holder 变动", "",
                  "| 代币 | 7d留存% | 14d留存% | 新进 | 退出 | Top10% | Δ7d | Δ14d | 吸筹Δ | 评级 |",
                  "|------|---------|---------|------|------|--------|-----|------|-------|------|"]
        for r in acc_results[:25]:
            if r["retention_7d"] >= 90: grade = "🔒极稳"
            elif r["retention_7d"] >= 75: grade = "✅稳定"
            elif r["retention_7d"] >= 60: grade = "⚠波动"
            else: grade = "❌流失"
            r14_str = f"{r['retention_14d']:.0f}%" if r["retention_14d"] is not None else "—"
            t10_14 = f"{r['top10_delta_14d']:+.1f}%" if r["top10_delta_14d"] is not None else "—"
            lines.append(
                f"| {r['symbol']} | {r['retention_7d']:.0f}% | {r14_str} "
                f"| {r['entered_7d']} | {r['exited_7d']} "
                f"| {r['top10_now']:.1f}% | {r['top10_delta_7d']:+.1f}% | {t10_14} "
                f"| {r['acc_delta_7d']:+d} | {grade} |"
            )

    # 异常流动
    danger = [r for r in results if r["retention_7d"] < 60 and r["symbol"]]
    if danger:
        lines += ["", "### ⚠ 异常流动（Top50 留存 < 60%）", "",
                  "| 代币 | 7d留存% | 14d留存% | 退出数 | Top10Δ | 含义 |",
                  "|------|---------|---------|--------|--------|------|"]
        for r in danger[:10]:
            meaning = "大规模换手" if r["top10_delta_7d"] < -5 else "底部换手"
            r14_str = f"{r['retention_14d']:.0f}%" if r["retention_14d"] is not None else "—"
            lines.append(f"| {r['symbol']} | {r['retention_7d']:.0f}% | {r14_str} | {r['exited_7d']} | {r['top10_delta_7d']:+.1f}% | {meaning} |")

    # P-ENRICH: 遗漏检测 — 留存<50% 但 meta 未标 DIST
    missed = [r for r in results if r["retention_7d"] < 50 and r["symbol"]
              and r["meta_verdict"] not in ("DIST", "")]
    if missed:
        lines += ["", "### 🚨 遗漏检测（留存<50% 但 meta 未标 DIST）", "",
                  "| 代币 | 7d留存% | Top10Δ | meta判定 | 建议 |",
                  "|------|---------|--------|---------|------|"]
        for r in missed[:10]:
            lines.append(
                f"| {r['symbol']} | {r['retention_7d']:.0f}% "
                f"| {r['top10_delta_7d']:+.1f}% | {r['meta_verdict']} | 应标记 DIST |"
            )

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
# P-DB: token_history 持久化
# ══════════════════════════════════════════════════════════════
def ensure_token_history_table(sumdb):
    """创建 token_history 表"""
    sumdb.execute("""
        CREATE TABLE IF NOT EXISTS token_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            computed_date   TEXT NOT NULL,
            chain           TEXT NOT NULL DEFAULT '',
            token_address   TEXT NOT NULL DEFAULT '',
            token_symbol    TEXT,
            signal_first_seen TEXT,
            signal_level    TEXT,
            entry_price     REAL DEFAULT 0,
            price_7d_ret    REAL,
            price_14d_ret   REAL,
            price_now_ret   REAL,
            retention_7d    REAL,
            retention_14d   REAL,
            whale_entered   INTEGER DEFAULT 0,
            whale_exited    INTEGER DEFAULT 0,
            top10_pct       REAL,
            top10_delta     REAL,
            acc_count       INTEGER DEFAULT 0,
            acc_delta       INTEGER DEFAULT 0,
            score_slope     REAL,
            score_sigma     REAL,
            consec_acc      INTEGER DEFAULT 0,
            UNIQUE(token_address, computed_date)
        )
    """)
    sumdb.commit()


def save_token_history(sumdb, bt_data, mig_data, ts_data):
    """将计算结果写入 token_history"""
    today = datetime.now().strftime("%Y-%m-%d")

    # 索引化
    mig_map = {r["addr"]: r for r in mig_data if r.get("addr")}
    ts_map = {r["symbol"]: r for r in ts_data}

    count = 0
    for bt in bt_data:
        addr = bt.get("addr", "")
        sym = bt["symbol"]
        mig = mig_map.get(addr, {})
        ts = ts_map.get(sym, {})

        try:
            sumdb.execute("""
                INSERT OR REPLACE INTO token_history
                (computed_date, token_address, token_symbol, signal_first_seen,
                 signal_level, entry_price, price_7d_ret, price_14d_ret, price_now_ret,
                 retention_7d, retention_14d, whale_entered, whale_exited,
                 top10_pct, top10_delta, acc_count, acc_delta,
                 score_slope, score_sigma, consec_acc)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                today, addr, sym, bt.get("date"),
                bt["signal"], bt["entry"], bt.get("ret_7d"), bt.get("ret_14d"), bt.get("ret_now"),
                mig.get("retention_7d"), mig.get("retention_14d"),
                mig.get("entered_7d", 0), mig.get("exited_7d", 0),
                mig.get("top10_now"), mig.get("top10_delta_7d"),
                mig.get("acc_now", 0), mig.get("acc_delta_7d", 0),
                ts.get("slope"), ts.get("sigma"), ts.get("consec_acc", 0),
            ))
            count += 1
        except Exception:
            pass

    sumdb.commit()
    return count


# ══════════════════════════════════════════════════════════════
# P-ENRICH: 单币画像（Top ACC 代币完整档案）
# ══════════════════════════════════════════════════════════════
def coin_profile(bt_data, mig_data, ts_data, sumdb):
    """Top 10 ACC 代币的完整画像"""
    # 找 ACC 代币中 meta_score 最高的
    meta_map = {}
    try:
        for r in sumdb.execute(
            "SELECT token_symbol, meta_score, meta_verdict, engine_hits, stage "
            "FROM meta_snapshots WHERE scan_time=(SELECT MAX(scan_time) FROM meta_snapshots)"
        ):
            meta_map[r["token_symbol"]] = dict(r)
    except Exception:
        pass

    acc_tokens = [(sym, info) for sym, info in meta_map.items()
                  if info.get("meta_verdict") == "ACC"]
    acc_tokens.sort(key=lambda x: x[1].get("meta_score", 0), reverse=True)

    if not acc_tokens:
        return ""

    mig_sym_map = {r["symbol"]: r for r in mig_data if r.get("symbol")}
    ts_map = {r["symbol"]: r for r in ts_data}
    bt_map = {r["symbol"]: r for r in bt_data}

    lines = ["## 🎯 Top ACC 单币画像", ""]

    for sym, meta in acc_tokens[:10]:
        mig = mig_sym_map.get(sym, {})
        ts = ts_map.get(sym, {})
        bt = bt_map.get(sym, {})

        lines.append(f"### {sym}")
        lines.append("")
        lines.append("| 维度 | 数据 |")
        lines.append("|------|------|")
        lines.append(f"| meta 综合分 | {meta.get('meta_score', 0):.1f} |")
        lines.append(f"| 引擎命中 | {meta.get('engine_hits', 0)} |")
        lines.append(f"| 生命周期 | {meta.get('stage', '—')} |")

        if bt:
            lines.append(f"| 首次信号日 | {bt.get('date', '—')} |")
            lines.append(f"| 入场价 | ${bt.get('entry', 0):.4f} |")
            ret_now = bt.get('ret_now')
            lines.append(f"| 至今收益 | {ret_now:+.1f}% |" if ret_now is not None else "| 至今收益 | — |")

        if mig:
            lines.append(f"| 7d 留存率 | {mig.get('retention_7d', 0):.0f}% |")
            r14 = mig.get('retention_14d')
            lines.append(f"| 14d 留存率 | {r14:.0f}% |" if r14 is not None else "| 14d 留存率 | — |")
            lines.append(f"| Top10 集中度 | {mig.get('top10_now', 0):.1f}% (Δ{mig.get('top10_delta_7d', 0):+.1f}%) |")
            lines.append(f"| 吸筹数变化 | {mig.get('acc_delta_7d', 0):+d} |")

        if ts:
            lines.append(f"| 积分斜率 | {ts.get('slope', 0):+.2f} |")
            lines.append(f"| 连续 ACC | {ts.get('consec_acc', 0)} 轮 |")
            lines.append(f"| 序列 | {ts.get('last5', '—')} |")

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

    # P-DB: 确保表存在
    ensure_token_history_table(sumdb)

    # 模块 1
    print("  [1/5] 信号回测（P-FIX: 首次信号时间）...")
    bt_md, bt_data = backtest_watchlist(src, sumdb)
    print(f"        {len(bt_data)} 个代币有回测数据")

    # 模块 2
    print("  [2/5] holder 迁移（14d + 遗漏检测）...")
    mig_md, mig_data = migration_analysis(src, sumdb)
    print(f"        {len(mig_data)} 个代币有迁移数据")

    # 模块 3
    print("  [3/5] 积分时序...")
    ts_md, ts_data = score_timeseries(sumdb)
    print(f"        {len(ts_data)} 个代币有时序数据")

    # 模块 4
    print("  [4/5] 信号×收益...")
    corr_md = signal_price_corr(bt_data, sumdb)

    # 模块 5
    print("  [5/5] 单币画像...")
    profile_md = coin_profile(bt_data, mig_data, ts_data, sumdb)

    # P-DB: 写入 token_history
    saved = save_token_history(sumdb, bt_data, mig_data, ts_data)
    print(f"  [DB] token_history 写入 {saved} 行")

    # 组装报告
    today = datetime.now().strftime("%Y-%m-%d")
    header = f"""# 📊 AI-SUM 长期分析报告 — {today}

> 时间基准: 首次信号时间（meta_snapshots/unified_results）
> 数据源: bubblemap + gecko + meta {len(ts_data)}代币
> 生成: history_report.py | 耗时: {{elapsed:.1f}}s

---

"""
    parts = [bt_md, mig_md, ts_md, corr_md]
    if profile_md:
        parts.append(profile_md)
    body = "\n---\n\n".join(parts)
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

