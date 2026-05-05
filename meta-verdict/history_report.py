#!/usr/bin/env python3
"""
AI-SUM 长期分析报告 V3.0
9 模块融合: 信号回测 + holder迁移 + 积分时序 + 信号质量
           + 流动性健康 + 价格风险 + 失败案例 + 漏网之鱼 + 单币画像
只读 select.db，可写 select-sum.db
输出: /opt/AI-SUM/report/history/history_YYYYMMDD.md
"""
import sqlite3
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
import math

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

        # 1d 后价格
        d1 = (sd + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        p1 = src.execute(
            "SELECT price_usd FROM gecko_market_data "
            "WHERE token_address=? AND scan_time>=? AND price_usd>0 "
            "ORDER BY scan_time LIMIT 1", (addr, d1)
        ).fetchone()

        # 3d 后价格
        d3 = (sd + timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        p3 = src.execute(
            "SELECT price_usd FROM gecko_market_data "
            "WHERE token_address=? AND scan_time>=? AND price_usd>0 "
            "ORDER BY scan_time LIMIT 1", (addr, d3)
        ).fetchone()

        # MDD: 信号后所有价格中的最低点
        all_prices = src.execute(
            "SELECT price_usd FROM gecko_market_data "
            "WHERE token_address=? AND scan_time>=? AND price_usd>0",
            (addr, sig_date)
        ).fetchall()
        mdd = None
        if all_prices and entry > 0:
            min_price = min(p[0] for p in all_prices)
            mdd = (min_price - entry) / entry * 100

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
            "ret_1d": ((p1[0] - entry) / entry * 100) if p1 else None,
            "ret_3d": ((p3[0] - entry) / entry * 100) if p3 else None,
            "ret_7d": ((p7[0] - entry) / entry * 100) if p7 else None,
            "ret_14d": ((p14[0] - entry) / entry * 100) if p14 else None,
            "ret_now": ((pnow[0] - entry) / entry * 100) if pnow else None,
            "price_now": pnow[0] if pnow else 0,
            "days_held": days_held,
            "addr": addr,
            "mdd": mdd,
        }
        results.append(r)

    # 汇总
    lines = ["## 📊 信号回测（首次信号时间 × 收益验证）", ""]
    lines.append(f"> 时间基准: meta_snapshots/unified_results 首次信号时间 | 过滤当天信号 {skipped_today} 个")
    lines.append("")

    # 按信号分组统计
    def _wr(lst): return f"{sum(1 for x in lst if x>0)/len(lst)*100:.0f}%" if lst else "—"
    def _avg(lst): return f"{sum(lst)/len(lst):+.1f}%" if lst else "—"
    def _med(lst): return f"{statistics.median(lst):+.1f}%" if lst else "—"

    lines += ["### 汇总", "",
              "| 信号 | 样本 | 1d胜率 | 3d胜率 | 7d胜率 | 至今胜率 | 至今均收 | 至今中位 | MDD中位 |",
              "|------|------|--------|--------|--------|---------|---------|---------|---------|"]
    # 全量 precision 统计
    all_acc_rnow = [r["ret_now"] for r in results if r["ret_now"] is not None]
    precision = f"{sum(1 for x in all_acc_rnow if x > 0) / len(all_acc_rnow) * 100:.0f}%" if all_acc_rnow else "—"
    lines.append(f"> 信号整体 Precision（盈利/总信号）: **{precision}** ({sum(1 for x in all_acc_rnow if x > 0)}/{len(all_acc_rnow)})")
    lines.append("")
    for sig in ["DIAMOND", "RED", "YELLOW"]:
        grp = [r for r in results if r["signal"] == sig]
        if not grp:
            continue
        r1 = [r["ret_1d"] for r in grp if r["ret_1d"] is not None]
        r3 = [r["ret_3d"] for r in grp if r["ret_3d"] is not None]
        r7 = [r["ret_7d"] for r in grp if r["ret_7d"] is not None]
        rnow = [r["ret_now"] for r in grp if r["ret_now"] is not None]
        mdds = [r["mdd"] for r in grp if r["mdd"] is not None]
        lines.append(f"| {sig} | {len(grp)} | {_wr(r1)} | {_wr(r3)} | {_wr(r7)} | {_wr(rnow)} | {_avg(rnow)} | {_med(rnow)} | {_med(mdds)} |")

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

        # P4: 14d 有效性校验 — old14 和 old7 差距 < 3天时标记不可靠
        r14_reliable = True
        if r14 and old14 and old7:
            try:
                gap_days = abs((datetime.strptime(old7[:19], "%Y-%m-%d %H:%M:%S")
                               - datetime.strptime(old14[:19], "%Y-%m-%d %H:%M:%S")).days)
                if gap_days < 3:
                    r14_reliable = False
            except Exception:
                pass

        row = {
            "symbol": sym, "addr": addr,
            "retention_7d": r7["retention"],
            "entered_7d": r7["entered"], "exited_7d": r7["exited"],
            "top10_now": r7["top10_now"], "top10_delta_7d": r7["top10_delta"],
            "acc_now": r7["acc_now"], "acc_delta_7d": r7["acc_delta"],
            "retention_14d": r14["retention"] if r14 and r14_reliable else None,
            "top10_delta_14d": r14["top10_delta"] if r14 and r14_reliable else None,
            "acc_delta_14d": r14["acc_delta"] if r14 and r14_reliable else None,
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

    # P1: 遗漏检测 — 留存<50% 且 Top10Δ<0 但 meta 未标 DIST（排除底部吸筹假阳性）
    real_missed = [r for r in results if r["retention_7d"] < 50 and r["symbol"]
                   and r["meta_verdict"] not in ("DIST", "")
                   and r["top10_delta_7d"] < 0]  # Top10在减少才是真出货
    bottom_acc = [r for r in results if r["retention_7d"] < 50 and r["symbol"]
                  and r["meta_verdict"] not in ("DIST", "")
                  and r["top10_delta_7d"] >= 0]  # Top10在增加=底部吸筹
    if real_missed:
        lines += ["", "### 🚨 遗漏检测（留存<50% + Top10下降 但 meta≠DIST）", "",
                  "| 代币 | 7d留存% | Top10Δ | meta判定 | 建议 |",
                  "|------|---------|--------|---------|------|"]
        for r in real_missed[:10]:
            lines.append(
                f"| {r['symbol']} | {r['retention_7d']:.0f}% "
                f"| {r['top10_delta_7d']:+.1f}% | {r['meta_verdict']} | 应标记 DIST |"
            )
    if bottom_acc:
        lines += ["", "### 💡 底部吸筹（留存<50% 但 Top10 增加）", "",
                  "| 代币 | 7d留存% | Top10Δ | meta判定 | 含义 |",
                  "|------|---------|--------|---------|------|"]
        for r in bottom_acc[:10]:
            lines.append(
                f"| {r['symbol']} | {r['retention_7d']:.0f}% "
                f"| {r['top10_delta_7d']:+.1f}% | {r['meta_verdict']} | 散户换手，大户加仓 |"
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

        # P2: 平序列修复 — σ < 0.05 时 slope 强制归零（V3: 从0.1放宽到0.05）
        if sigma < 0.05:
            slope = 0.0

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
        # P3: 样本 < 5 时标注
        note = " ⚠" if len(grp) < 5 else ""
        lines.append(f"| {name}{note} | {len(grp)} | {wr(r7)} | {avg(r7)} | {wr(rnow)} | {avg(rnow)} |")

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
# 模块 6: 流动性健康度（V3 新增）
# ══════════════════════════════════════════════════════════════
def liquidity_health(src, sumdb):
    """激活 gecko_market_data 闲置字段: volume/reserve/buy_tx_pct"""
    meta_map = {}
    addr_map = {}
    try:
        for r in sumdb.execute(
            "SELECT token_symbol, token_address, meta_verdict FROM meta_snapshots "
            "WHERE scan_time=(SELECT MAX(scan_time) FROM meta_snapshots) AND meta_verdict='ACC'"
        ):
            meta_map[r["token_symbol"]] = r["meta_verdict"]
            addr_map[r["token_symbol"]] = r["token_address"]
    except Exception:
        pass

    sym_addr = {}
    for r in sumdb.execute("SELECT token_address, token_symbol FROM watchlist"):
        sym_addr[r["token_symbol"]] = r["token_address"]
    # merge
    for sym, addr in addr_map.items():
        if sym not in sym_addr:
            sym_addr[sym] = addr

    results = []
    for sym in meta_map:
        addr = sym_addr.get(sym, "")
        if not addr:
            continue
        # 最新一条
        latest = src.execute(
            "SELECT volume_24h, reserve_usd, buy_tx_pct, buys_24h, sells_24h, "
            "buyers_24h, sellers_24h, market_cap_usd, price_change_24h "
            "FROM gecko_market_data WHERE token_address=? ORDER BY scan_time DESC LIMIT 1",
            (addr,)
        ).fetchone()
        if not latest:
            continue
        vol = latest["volume_24h"] or 0
        reserve = latest["reserve_usd"] or 0
        buy_pct = latest["buy_tx_pct"] or 0
        mcap = latest["market_cap_usd"] or 0
        price_chg = latest["price_change_24h"] or 0
        buys = latest["buys_24h"] or 0
        sells = latest["sells_24h"] or 0
        buyers = latest["buyers_24h"] or 0
        sellers = latest["sellers_24h"] or 0

        # 7d 均量
        rows7 = src.execute(
            "SELECT volume_24h FROM gecko_market_data "
            "WHERE token_address=? AND volume_24h>0 ORDER BY scan_time DESC LIMIT 14",
            (addr,)
        ).fetchall()
        avg7_vol = sum(r[0] for r in rows7) / max(len(rows7), 1) if rows7 else 0
        vol_change = ((vol - avg7_vol) / avg7_vol * 100) if avg7_vol > 0 else 0

        turnover = (vol / mcap * 100) if mcap > 0 else 0
        bs_ratio = buyers / max(sellers, 1)

        # 量价背离检测
        vpd = ""
        if price_chg > 3 and vol_change < -30:
            vpd = "价涨量缩"
        elif price_chg < -3 and vol_change > 30:
            vpd = "价跌量增"

        # 评级
        if vol < 1000 or (avg7_vol > 0 and vol < avg7_vol * 0.3):
            grade = "❌枯竭"
        elif reserve < 10000:
            grade = "⚠低LP"
        elif vpd:
            grade = "⚠背离"
        else:
            grade = "✅正常"

        results.append({
            "symbol": sym, "vol_24h": vol, "avg7_vol": avg7_vol,
            "vol_change": vol_change, "reserve": reserve,
            "turnover": turnover, "buy_pct": buy_pct,
            "bs_ratio": bs_ratio, "vpd": vpd, "grade": grade,
            "price_chg": price_chg, "addr": addr,
        })

    results.sort(key=lambda r: r["vol_24h"], reverse=True)

    lines = ["## 💧 流动性健康度", ""]
    if not results:
        lines.append("> 无 ACC 代币流动性数据")
        return "\n".join(lines), results

    lines += ["### ACC 代币交易活跃度", "",
              "| 代币 | 24h量 | 7d均量 | 量变化 | LP深度 | 换手率 | 买入占比 | 买/卖人数 | 评级 |",
              "|------|-------|--------|--------|--------|--------|---------|----------|------|"]
    for r in results[:25]:
        def _fmtk(v):
            if v >= 1e6: return f"${v/1e6:.1f}M"
            if v >= 1e3: return f"${v/1e3:.0f}K"
            return f"${v:.0f}"
        lines.append(
            f"| {r['symbol']} | {_fmtk(r['vol_24h'])} | {_fmtk(r['avg7_vol'])} "
            f"| {r['vol_change']:+.0f}% | {_fmtk(r['reserve'])} | {r['turnover']:.1f}% "
            f"| {r['buy_pct']:.0f}% | {r['bs_ratio']:.1f}x | {r['grade']} |"
        )

    # 量价背离
    vpd_list = [r for r in results if r["vpd"]]
    if vpd_list:
        lines += ["", "### ⚠ 量价背离", "",
                  "| 代币 | 24h价格 | 量变化 | 类型 | 含义 |",
                  "|------|---------|--------|------|------|"]
        for r in vpd_list:
            meaning = "拉盘无力" if r["vpd"] == "价涨量缩" else "恐慌抛售"
            lines.append(f"| {r['symbol']} | {r['price_chg']:+.1f}% | {r['vol_change']:+.0f}% | {r['vpd']} | {meaning} |")

    # 流动性枯竭
    dry = [r for r in results if r["grade"] == "❌枯竭"]
    if dry:
        lines += ["", "### ❌ 流动性枯竭", "",
                  "| 代币 | 24h量 | 7d均量 | 衰减率 | LP | 风险 |",
                  "|------|-------|--------|--------|-----|------|"]
        for r in dry:
            decay = f"{r['vol_change']:.0f}%" if r['avg7_vol'] > 0 else "—"
            lines.append(f"| {r['symbol']} | {r['vol_24h']:.0f} | {r['avg7_vol']:.0f} | {decay} | ${r['reserve']:.0f} | 交易几乎停滞 |")

    lines.append("")
    return "\n".join(lines), results


# ══════════════════════════════════════════════════════════════
# 模块 7: 信号质量评估（V3: 替代旧模块4）
# ══════════════════════════════════════════════════════════════
def signal_quality(backtest_results, sumdb):
    """引擎组合矩阵 + precision/recall + 失败案例 + 漏网之鱼"""
    # 获取 meta 分项积分
    meta_detail = {}
    for r in sumdb.execute("""
        SELECT token_symbol, meta_score, meta_verdict, engine_hits,
               master_score, opus_score, unified_score, whale_score, cb_score
        FROM meta_snapshots WHERE scan_time=(SELECT MAX(scan_time) FROM meta_snapshots)
    """):
        meta_detail[r["token_symbol"]] = dict(r)

    # 合并回测+meta
    merged = []
    for bt in backtest_results:
        m = meta_detail.get(bt["symbol"])
        if m:
            bt["meta_score"] = m["meta_score"]
            bt["meta_verdict"] = m["meta_verdict"]
            bt["engines"] = m["engine_hits"]
            bt["master_score"] = m["master_score"]
            bt["opus_score"] = m["opus_score"]
            bt["unified_score"] = m["unified_score"]
            bt["whale_score"] = m["whale_score"]
            bt["cb_score"] = m["cb_score"]
            merged.append(bt)

    lines = ["## 🔬 信号质量评估", ""]

    if not merged:
        lines.append("> 无可分析数据")
        return "\n".join(lines)

    # --- 引擎组合矩阵 ---
    combo_stats = defaultdict(list)
    for bt in merged:
        engines = []
        if bt.get("master_score", 0) > 0: engines.append("M")
        if bt.get("opus_score", 0) > 0: engines.append("O")
        if bt.get("unified_score", 0) > 0: engines.append("U")
        if bt.get("whale_score", 0) > 0: engines.append("W")
        if bt.get("cb_score", 0) > 0: engines.append("CB")
        key = "+".join(engines) if engines else "无引擎"
        combo_stats[key].append(bt)

    lines += ["### 引擎组合胜率矩阵", "",
              "| 引擎组合 | 样本 | 至今胜率 | 至今均收 | 至今中位 | MDD中位 |",
              "|----------|------|---------|---------|---------|---------|"]
    sorted_combos = sorted(combo_stats.items(), key=lambda x: len(x[1]), reverse=True)
    for combo, items in sorted_combos[:12]:
        rnow = [r["ret_now"] for r in items if r["ret_now"] is not None]
        mdds = [r["mdd"] for r in items if r["mdd"] is not None]
        if not rnow:
            continue
        wr = f"{sum(1 for x in rnow if x > 0) / len(rnow) * 100:.0f}%"
        avg_r = f"{sum(rnow) / len(rnow):+.1f}%"
        med_r = f"{statistics.median(rnow):+.1f}%"
        med_mdd = f"{statistics.median(mdds):+.1f}%" if mdds else "—"
        note = " ⚠" if len(items) < 5 else ""
        lines.append(f"| {combo}{note} | {len(items)} | {wr} | {avg_r} | {med_r} | {med_mdd} |")

    # --- 单引擎 precision ---
    lines += ["", "### 单引擎 Precision", "",
              "| 引擎 | 命中数 | 盈利数 | Precision |",
              "|------|--------|--------|-----------|"]
    for eng_name, eng_key in [("master", "master_score"), ("opus", "opus_score"),
                               ("unified", "unified_score"), ("whale", "whale_score"),
                               ("CB", "cb_score")]:
        hit = [r for r in merged if r.get(eng_key, 0) > 0]
        if not hit:
            continue
        profitable = [r for r in hit if r.get("ret_now") is not None and r["ret_now"] > 0]
        p = f"{len(profitable) / len(hit) * 100:.0f}%" if hit else "—"
        lines.append(f"| {eng_name} | {len(hit)} | {len(profitable)} | {p} |")

    # --- 信号 P/R/F1 ---
    acc_items = [r for r in merged if r.get("meta_verdict") == "ACC" and r.get("ret_now") is not None]
    all_profitable = [r for r in merged if r.get("ret_now") is not None and r["ret_now"] > 0]
    acc_profitable = [r for r in acc_items if r["ret_now"] > 0]
    if acc_items and all_profitable:
        prec = len(acc_profitable) / len(acc_items) * 100
        recall = len(acc_profitable) / max(len(all_profitable), 1) * 100
        f1 = 2 * prec * recall / max(prec + recall, 1)
        lines += ["", "### 信号 Precision / Recall / F1", "",
                  "| 指标 | 值 | 说明 |",
                  "|------|-----|------|",
                  f"| Precision | {prec:.0f}% | ACC中盈利: {len(acc_profitable)}/{len(acc_items)} |",
                  f"| Recall | {recall:.0f}% | 盈利中被ACC命中: {len(acc_profitable)}/{len(all_profitable)} |",
                  f"| F1 | {f1:.0f}% | 综合准确率 |"]

    # --- 失败案例 ---
    failures = [r for r in merged if r.get("meta_verdict") == "ACC"
                and r.get("ret_now") is not None and r["ret_now"] < -15]
    if failures:
        failures.sort(key=lambda r: r["ret_now"])
        lines += ["", "### 💀 失败案例（ACC 但亏损 >15%）", "",
                  "| 代币 | 综合分 | 引擎 | 至今收益 | MDD | 信号日 |",
                  "|------|--------|------|---------|-----|--------|"]
        for r in failures[:10]:
            engines = []
            if r.get("master_score", 0) > 0: engines.append("M")
            if r.get("opus_score", 0) > 0: engines.append("O")
            if r.get("whale_score", 0) > 0: engines.append("W")
            if r.get("cb_score", 0) > 0: engines.append("CB")
            eng_str = "+".join(engines) if engines else "—"
            mdd_str = f"{r['mdd']:+.1f}%" if r.get("mdd") is not None else "—"
            lines.append(f"| {r['symbol']} | {r.get('meta_score',0):.1f} | {eng_str} | {r['ret_now']:+.1f}% | {mdd_str} | {r['date']} |")

    # --- 漏网之鱼 ---
    neutral_profit = [r for r in merged if r.get("meta_verdict") != "ACC"
                      and r.get("ret_now") is not None and r["ret_now"] > 30]
    if neutral_profit:
        neutral_profit.sort(key=lambda r: r["ret_now"], reverse=True)
        lines += ["", "### 🔍 漏网之鱼（非ACC 但暴涨 >30%）", "",
                  "| 代币 | meta判定 | 综合分 | 至今收益 | 信号 | 遗漏原因 |",
                  "|------|---------|--------|---------|------|---------|"]
        for r in neutral_profit[:10]:
            reason = "引擎未覆盖" if r.get("engines", 0) <= 1 else "积分未达阈值"
            lines.append(
                f"| {r['symbol']} | {r.get('meta_verdict','—')} | {r.get('meta_score',0):.1f} "
                f"| {r['ret_now']:+.1f}% | {r['signal']} | {reason} |"
            )

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
            -- V3 新增字段
            volume_24h      REAL DEFAULT 0,
            reserve_usd     REAL DEFAULT 0,
            buy_tx_pct      REAL DEFAULT 0,
            turnover_ratio  REAL DEFAULT 0,
            mdd             REAL,
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


def save_token_history(sumdb, bt_data, mig_data, ts_data, liq_data=None):
    """将计算结果写入 token_history（V3: 增加流动性+MDD+meta补漏）"""
    today = datetime.now().strftime("%Y-%m-%d")

    # V3: 扩展字段（兼容旧表）
    for col, typedef in [("volume_24h", "REAL DEFAULT 0"), ("reserve_usd", "REAL DEFAULT 0"),
                          ("buy_tx_pct", "REAL DEFAULT 0"), ("turnover_ratio", "REAL DEFAULT 0"),
                          ("mdd", "REAL")]:
        try:
            sumdb.execute(f"ALTER TABLE token_history ADD COLUMN {col} {typedef}")
        except Exception:
            pass

    # 索引化
    mig_map = {r["addr"]: r for r in mig_data if r.get("addr")}
    ts_map = {r["symbol"]: r for r in ts_data}
    liq_map = {r["symbol"]: r for r in (liq_data or [])}

    count = 0
    written_addrs = set()
    written_syms = set()

    def _insert(addr, sym, bt, mig, ts, liq):
        nonlocal count
        try:
            sumdb.execute("""
                INSERT OR REPLACE INTO token_history
                (computed_date, token_address, token_symbol, signal_first_seen,
                 signal_level, entry_price, price_7d_ret, price_14d_ret, price_now_ret,
                 retention_7d, retention_14d, whale_entered, whale_exited,
                 top10_pct, top10_delta, acc_count, acc_delta,
                 score_slope, score_sigma, consec_acc,
                 volume_24h, reserve_usd, buy_tx_pct, turnover_ratio, mdd)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                today, addr, sym, bt.get("date") if bt else None,
                bt.get("signal", "NONE") if bt else "NONE",
                bt.get("entry", 0) if bt else 0,
                bt.get("ret_7d") if bt else None,
                bt.get("ret_14d") if bt else None,
                bt.get("ret_now") if bt else None,
                mig.get("retention_7d"), mig.get("retention_14d"),
                mig.get("entered_7d", 0), mig.get("exited_7d", 0),
                mig.get("top10_now"), mig.get("top10_delta_7d"),
                mig.get("acc_now", 0), mig.get("acc_delta_7d", 0),
                ts.get("slope"), ts.get("sigma"), ts.get("consec_acc", 0),
                liq.get("vol_24h", 0), liq.get("reserve", 0),
                liq.get("buy_pct", 0), liq.get("turnover", 0),
                bt.get("mdd") if bt else None,
            ))
            count += 1
            written_addrs.add(addr)
            written_syms.add(sym)
        except Exception:
            pass

    # 1. 写入有回测数据的代币
    for bt in bt_data:
        addr = bt.get("addr", "")
        sym = bt["symbol"]
        _insert(addr, sym, bt, mig_map.get(addr, {}), ts_map.get(sym, {}), liq_map.get(sym, {}))

    # 2. 写入有迁移数据但无回测数据的代币
    for mig in mig_data:
        addr = mig.get("addr", "")
        if addr in written_addrs or not mig.get("symbol"):
            continue
        sym = mig["symbol"]
        _insert(addr, sym, None, mig, ts_map.get(sym, {}), liq_map.get(sym, {}))

    # V3: 3. meta_snapshots ACC/DIST 全量补漏
    try:
        meta_rows = sumdb.execute(
            "SELECT token_symbol, token_address FROM meta_snapshots "
            "WHERE scan_time=(SELECT MAX(scan_time) FROM meta_snapshots) "
            "AND meta_verdict IN ('ACC','DIST')"
        ).fetchall()
        for r in meta_rows:
            sym, addr = r["token_symbol"], r["token_address"]
            if sym in written_syms or addr in written_addrs:
                continue
            _insert(addr, sym, None, {}, ts_map.get(sym, {}), liq_map.get(sym, {}))
    except Exception:
        pass

    sumdb.commit()
    return count


# ══════════════════════════════════════════════════════════════
# P-ENRICH: 单币画像（Top ACC 代币完整档案）
# ══════════════════════════════════════════════════════════════
def coin_profile(bt_data, mig_data, ts_data, sumdb, liq_data=None):
    """Top 10 ACC 代币的完整画像（V3: 增加流动性+MDD）"""
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
    liq_map = {r["symbol"]: r for r in (liq_data or [])}

    # P7: 读取昨天 token_history
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    yday_map = {}
    try:
        for r in sumdb.execute(
            "SELECT token_symbol, price_now_ret, retention_7d FROM token_history WHERE computed_date=?",
            (yesterday,)
        ):
            yday_map[r["token_symbol"]] = {"ret": r["price_now_ret"], "retention": r["retention_7d"]}
    except Exception:
        pass

    lines = ["## 🎯 Top ACC 单币画像", ""]
    if yday_map:
        lines.append(f"> vs 昨日对比: {yesterday} | {len(yday_map)} 个代币有历史数据")
    else:
        lines.append("> vs 昨日: 无历史数据（首次运行，明日开始生效）")
    lines.append("")

    for sym, meta in acc_tokens[:10]:
        mig = mig_sym_map.get(sym, {})
        ts = ts_map.get(sym, {})
        bt = bt_map.get(sym, {})
        yday = yday_map.get(sym, {})

        # P5: 留存异常标注
        warnings = []
        retention_7d = mig.get('retention_7d', 0)
        if retention_7d < 60 and retention_7d > 0:
            warnings.append(f"⚠ 7d留存仅{retention_7d:.0f}%")
        if mig.get('top10_delta_7d', 0) < -5:
            warnings.append(f"⚠ Top10Δ{mig.get('top10_delta_7d',0):+.1f}%")
        if mig.get('acc_delta_7d', 0) < -5:
            warnings.append(f"⚠ 吸筹数{mig.get('acc_delta_7d',0):+d}")

        title = f"### {sym}"
        if warnings:
            title += " " + " ".join(warnings)
        lines.append(title)
        lines.append("")
        lines.append("| 维度 | 数据 |")
        lines.append("|------|------|")
        lines.append(f"| meta 综合分 | {meta.get('meta_score', 0):.1f} |")
        lines.append(f"| 引擎命中 | {meta.get('engine_hits', 0)} |")
        lines.append(f"| 生命周期 | {meta.get('stage', '—')} |")

        # P5: 增加完整价格数据
        if bt:
            lines.append(f"| 首次信号日 | {bt.get('date', '—')} |")
            lines.append(f"| 入场价 | ${bt.get('entry', 0):.4f} |")
            lines.append(f"| 现价 | ${bt.get('price_now', 0):.4f} |")
            r7 = bt.get('ret_7d')
            r14 = bt.get('ret_14d')
            ret_now = bt.get('ret_now')
            lines.append(f"| 7d 收益 | {r7:+.1f}% |" if r7 is not None else "| 7d 收益 | — |")
            lines.append(f"| 14d 收益 | {r14:+.1f}% |" if r14 is not None else "| 14d 收益 | — |")
            ret_str = f"{ret_now:+.1f}%" if ret_now is not None else "—"
            # P7: vs 昨天收益对比
            if yday.get("ret") is not None and ret_now is not None:
                delta_ret = ret_now - yday["ret"]
                ret_str += f" (Δ{delta_ret:+.1f}%)"
            lines.append(f"| 至今收益 | {ret_str} |")
            lines.append(f"| 持有天数 | {bt.get('days_held', 0)}d |")

        if mig:
            ret_7d_str = f"{retention_7d:.0f}%"
            # P7: vs 昨天留存对比
            if yday.get("retention") is not None:
                delta_ret = retention_7d - yday["retention"]
                ret_7d_str += f" (Δ{delta_ret:+.0f}%)"
            lines.append(f"| 7d 留存率 | {ret_7d_str} |")
            r14 = mig.get('retention_14d')
            lines.append(f"| 14d 留存率 | {r14:.0f}% |" if r14 is not None else "| 14d 留存率 | — |")
            lines.append(f"| Top10 集中度 | {mig.get('top10_now', 0):.1f}% (Δ{mig.get('top10_delta_7d', 0):+.1f}%) |")
            lines.append(f"| 吸筹数变化 | {mig.get('acc_delta_7d', 0):+d} |")

        if ts:
            lines.append(f"| 积分斜率 | {ts.get('slope', 0):+.2f} |")
            lines.append(f"| 连续 ACC | {ts.get('consec_acc', 0)} 轮 |")
            lines.append(f"| 序列 | {ts.get('last5', '—')} |")

        # V3: 流动性+MDD
        liq = liq_map.get(sym, {})
        if liq:
            def _fk(v):
                if v >= 1e6: return f"${v/1e6:.1f}M"
                if v >= 1e3: return f"${v/1e3:.0f}K"
                return f"${v:.0f}"
            lines.append(f"| 24h交易量 | {_fk(liq.get('vol_24h', 0))} ({liq.get('vol_change',0):+.0f}%) |")
            lines.append(f"| LP深度 | {_fk(liq.get('reserve', 0))} |")
            lines.append(f"| 买入占比 | {liq.get('buy_pct', 0):.0f}% |")
            lines.append(f"| 流动性评级 | {liq.get('grade', '—')} |")
        if bt and bt.get("mdd") is not None:
            lines.append(f"| 最大回撤 | {bt['mdd']:+.1f}% |")

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
    print("  [1/9] 信号回测（1d/3d/7d/14d + MDD）...")
    bt_md, bt_data = backtest_watchlist(src, sumdb)
    print(f"        {len(bt_data)} 个代币有回测数据")

    # 模块 2
    print("  [2/9] holder 迁移（14d + 遗漏检测）...")
    mig_md, mig_data = migration_analysis(src, sumdb)
    print(f"        {len(mig_data)} 个代币有迁移数据")

    # 模块 3
    print("  [3/9] 积分时序...")
    ts_md, ts_data = score_timeseries(sumdb)
    print(f"        {len(ts_data)} 个代币有时序数据")

    # 模块 4
    print("  [4/9] 信号×收益分布...")
    corr_md = signal_price_corr(bt_data, sumdb)

    # 模块 5: 流动性健康度 (V3)
    print("  [5/9] 流动性健康度...")
    liq_md, liq_data = liquidity_health(src, sumdb)
    print(f"        {len(liq_data)} 个ACC代币有流动性数据")

    # 模块 6: 信号质量 (V3)
    print("  [6/9] 信号质量评估...")
    quality_md = signal_quality(bt_data, sumdb)

    # 模块 7: 单币画像
    print("  [7/9] 单币画像...")
    profile_md = coin_profile(bt_data, mig_data, ts_data, sumdb, liq_data)

    # P-DB: 写入 token_history（V3: 含流动性+MDD+meta补漏）
    saved = save_token_history(sumdb, bt_data, mig_data, ts_data, liq_data)
    print(f"  [DB] token_history 写入 {saved} 行")

    # 组装报告
    today = datetime.now().strftime("%Y-%m-%d")
    header = f"""# 📊 AI-SUM 长期分析报告 V3.0 — {today}

> 时间基准: 首次信号时间（meta_snapshots/unified_results）
> 数据源: bubblemap + gecko(20字段) + meta {len(ts_data)}代币
> 新增: 流动性健康度 | 信号质量评估 | 失败案例 | 漏网之鱼
> 生成: history_report.py V3 | 耗时: {{elapsed:.1f}}s

---

"""
    parts = [bt_md, mig_md, ts_md, corr_md, liq_md, quality_md]
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

