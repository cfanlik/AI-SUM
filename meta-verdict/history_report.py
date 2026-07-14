#!/usr/bin/env python3
"""
AI-SUM 长期分析报告 V3.0
9 模块融合: 信号回测 + holder迁移 + 积分时序 + 信号质量
           + 流动性健康 + 价格风险 + 失败案例 + 漏网之鱼 + 单币画像
只读 select.db，可写 select-sum.db
输出: /opt/AI-SUM/report/history/history_MMDD_HHMM.md (每6小时1份)
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


def format_price(val) -> str:
    if val is None:
        return "—"
    try:
        val = float(val)
    except (ValueError, TypeError):
        return "—"
    if val == 0:
        return "0"
    if val >= 1:
        return f"{val:.2f}"
    if val >= 0.01:
        return f"{val:.4f}"
    s = f"{val:.10f}".rstrip('0')
    if s.endswith('.'):
        s = s[:-1]
    if s == "0" or float(s) == 0:
        return f"{val:.2e}"
    return s


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

        # 入场价 (带链式时间最接近兜底价格)
        p0 = src.execute(
            "SELECT price_usd FROM gecko_market_data "
            "WHERE token_address=? AND scan_time>=? AND price_usd>0 "
            "ORDER BY scan_time LIMIT 1", (addr, sig_date)
        ).fetchone()
        
        if not p0 or p0[0] <= 0:
            # 链式时序最接近兜底价格 (找离信号时间绝对差最小的非 0 价格)
            p0_fb = src.execute(
                "SELECT price_usd FROM gecko_market_data "
                "WHERE token_address=? AND price_usd>0 "
                "ORDER BY ABS(strftime('%s', scan_time) - strftime('%s', ?)) LIMIT 1",
                (addr, sig_date)
            ).fetchone()
            if p0_fb and p0_fb[0] > 0:
                entry = p0_fb[0]
            else:
                continue
        else:
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

        # 动态 168h 滚动收益计算
        d_roll = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        p_roll = src.execute(
            "SELECT price_usd FROM gecko_market_data "
            "WHERE token_address=? AND scan_time>=? AND price_usd>0 "
            "ORDER BY scan_time LIMIT 1", (addr, d_roll)
        ).fetchone()
        
        rolling_7d_ret = None
        if p_roll and pnow:
            rolling_7d_ret = (pnow[0] - p_roll[0]) / p_roll[0] * 100
        else:
            # fallback 到 entry (信号发出尚不足 7d)
            if entry > 0 and pnow:
                rolling_7d_ret = (pnow[0] - entry) / entry * 100

        # Peak Return (7 天窗口内最高价格触及率)
        peak_return = None
        p_peaks = src.execute(
            "SELECT price_usd FROM gecko_market_data "
            "WHERE token_address=? AND scan_time>=? AND scan_time<=? AND price_usd>0",
            (addr, sig_date, d7)
        ).fetchall()
        if p_peaks and entry > 0:
            max_p = max(p[0] for p in p_peaks)
            peak_return = (max_p - entry) / entry * 100
        elif entry > 0 and pnow:
            peak_return = max(0.0, (pnow[0] - entry) / entry * 100)

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
            "rolling_7d_ret": rolling_7d_ret,
            "peak_return": peak_return,
        }
        results.append(r)

    # 计算 Market_Beta (有有效滚动收益代币的中位数涨跌)
    rollings = [r["rolling_7d_ret"] for r in results if r["rolling_7d_ret"] is not None]
    market_beta = 0.0
    if rollings:
        try:
            import statistics
            market_beta = statistics.median(rollings)
        except Exception:
            pass

    for r in results:
        if r["rolling_7d_ret"] is not None:
            r["alpha_ret"] = r["rolling_7d_ret"] - market_beta
        else:
            r["alpha_ret"] = None

    # 汇总
    lines = ["## 📊 信号回测（首次信号时间 × 收益验证）", ""]
    lines.append(f"> 时间基准: meta_snapshots/unified_results 首次信号时间 | 过滤当天信号 {skipped_today} 个")
    lines.append("")

    # 按信号分组统计
    def _wr(lst): return f"{sum(1 for x in lst if x>0)/len(lst)*100:.0f}%" if lst else "—"
    def _avg(lst): return f"{sum(lst)/len(lst):+.1f}%" if lst else "—"
    def _med(lst): return f"{statistics.median(lst):+.1f}%" if lst else "—"

    # 全量 precision 统计
    all_acc_rnow = [r["ret_now"] for r in results if r["ret_now"] is not None]
    precision = f"{sum(1 for x in all_acc_rnow if x > 0) / len(all_acc_rnow) * 100:.0f}%" if all_acc_rnow else "—"
    lines += ["### 汇总", "",
              f"> 信号 Precision（全信号口径: 盈利/总DIAMOND+RED+YELLOW）: **{precision}** ({sum(1 for x in all_acc_rnow if x > 0)}/{len(all_acc_rnow)})",
              "",
              "| 信号 | 样本 | 1d胜率 | 3d胜率 | 7d胜率 | 至今胜率 | 至今均收 | 至今中位 | MDD中位 |",
              "|------|------|--------|--------|--------|---------|---------|---------|---------|"]
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

    # 新增：滚动 168h 区间回测汇总渲染 (双轴重构)
    lines += ["", "### 动态滚动 168h 回测汇总", "",
              f"> 滚动大盘基准中位数收益 (Meme Market Beta): **{market_beta:+.1f}%**",
              "",
              "| 滚动信号 | 样本 | 滚动胜率 | 滚动均收 | 滚动中位 | 超额胜率 (Alpha WR) | 平均超额 (Avg Alpha) |",
              "|----------|------|----------|----------|----------|-------------------|-------------------|"]
    for sig in ["DIAMOND", "RED", "YELLOW"]:
        grp = [r for r in results if r["signal"] == sig]
        if not grp:
            continue
        r_roll = [r["rolling_7d_ret"] for r in grp if r["rolling_7d_ret"] is not None]
        r_alpha = [r["alpha_ret"] for r in grp if r["alpha_ret"] is not None]
        lines.append(f"| {sig} | {len(grp)} | {_wr(r_roll)} | {_avg(r_roll)} | {_med(r_roll)} | {_wr(r_alpha)} | {_avg(r_alpha)} |")

    # 明细(DIAMOND+RED)
    detail = [r for r in results if r["signal"] in ("DIAMOND", "RED")]
    detail.sort(key=lambda r: r["ret_now"] or 0, reverse=True)
    if detail:
        lines += ["", "### 明细（DIAMOND + RED）", "",
                  "| 代币 | 信号 | 首次信号日 | 持有天数 | 入场价 | 7d收益 | 14d收益 | 当前收益 | 滚动 7d 收益 |",
                  "|------|------|----------|---------|--------|--------|---------|---------|-------------|"]
        for r in detail[:20]:
            def fmt(v): return f"{v:+.1f}%" if v is not None else "—"
            sig_emoji = "💎" if r["signal"] == "DIAMOND" else "🔴"
            lines.append(f"| {r['symbol']} | {sig_emoji} | {r['date']} | {r['days_held']}d | ${format_price(r['entry'])} | {fmt(r['ret_7d'])} | {fmt(r['ret_14d'])} | {fmt(r['ret_now'])} | {fmt(r['rolling_7d_ret'])} |")

    lines.append("")
    return "\n".join(lines), results


# ══════════════════════════════════════════════════════════════
# 模块 2: Holder 迁移分析
# ══════════════════════════════════════════════════════════════
def migration_analysis(src, sumdb):
    """bubblemap 7d/14d holder 变动"""
    now = datetime.now()
    d1 = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    d3 = (now - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
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
            "AND rank<=100 AND is_cex=0 AND is_contract=0",
            (addr, latest)
        ).fetchall()
        old_rows = src.execute(
            "SELECT wallet_address, rank, hold_percentage, is_accumulating "
            "FROM bubblemap_holders WHERE token_address=? AND snapshot_time=? "
            "AND rank<=100 AND is_cex=0 AND is_contract=0",
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

        # V5: 24h/72h 快照
        old1 = src.execute(
            "SELECT MAX(snapshot_time) FROM bubblemap_holders WHERE token_address=? AND snapshot_time<=?",
            (addr, d1)
        ).fetchone()[0]
        old3 = src.execute(
            "SELECT MAX(snapshot_time) FROM bubblemap_holders WHERE token_address=? AND snapshot_time<=?",
            (addr, d3)
        ).fetchone()[0]

        r1 = _calc_retention(src, addr, latest, old1)
        r3 = _calc_retention(src, addr, latest, old3)

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
            "retention_24h": r1["retention"] if r1 else None,
            "retention_72h": r3["retention"] if r3 else None,
            "top10_delta_24h": r1["top10_delta"] if r1 else None,
            "top10_delta_72h": r3["top10_delta"] if r3 else None,
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
                  "| 代币 | 24h留存 | 72h留存 | 7d留存% | 14d留存% | Top10% | Δ24h | Δ7d | 吸筹Δ | 评级 |",
                  "|------|--------|--------|---------|---------|--------|------|-----|-------|------|"]
        for r in acc_results[:25]:
            r24 = f'{r["retention_24h"]:.0f}%' if r.get("retention_24h") is not None else "—"
            r72 = f'{r["retention_72h"]:.0f}%' if r.get("retention_72h") is not None else "—"
            d24 = f'{r["top10_delta_24h"]:+.1f}%' if r.get("top10_delta_24h") is not None else "—"
            if r["retention_7d"] >= 90: grade = "🔒极稳"
            elif r["retention_7d"] >= 75: grade = "✅稳定"
            elif r["retention_7d"] >= 60: grade = "⚠波动"
            else: grade = "❌流失"
            r14_str = f"{r['retention_14d']:.0f}%" if r["retention_14d"] is not None else "—"
            lines.append(
                f"| {r['symbol']} | {r24} | {r72} "
                f"| {r['retention_7d']:.0f}% | {r14_str} "
                f"| {r['top10_now']:.1f}% | {d24} | {r['top10_delta_7d']:+.1f}% "
                f"| {r['acc_delta_7d']:+d} | {grade} |"
            )

    # 异常流动
    danger = [r for r in results if r["retention_7d"] < 60 and r["symbol"]]
    if danger:
        lines += ["", "### ⚠ 异常流动（Top100 留存 < 60%）", "",
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
# 积分时序分析
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
            "rounds": len(snapshots), "last5": last5,
            "peak": max(scores), "trough": min(scores),
            "verdict": snapshots[-1]["meta_verdict"],
        })

    lines = ["## 📈 积分时序分析", ""]

    # 信号增强（V4.0: 过滤近5轮全相同的常量序列）
    rising = sorted([r for r in results if r["slope"] > 0.02 and r["verdict"] == "ACC"
                     and len(set(r["last5"].split("→"))) > 1],  # 排除常量
                    key=lambda r: r["slope"], reverse=True)
    if rising:
        lines += ["### 信号增强 Top 10", "",
                  "| 代币 | 当前分 | 斜率 | 方向 | σ(全量) | 连续ACC | 引擎 | 序列(最近5轮) |",
                  "|------|--------|------|------|---------|---------|------|-------------|"]
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
# 价格/信号相关性分析
# ══════════════════════════════════════════════════════════════
def signal_price_corr(backtest_results, sumdb):
    """V4.0: 已合并到 signal_quality, 保留空壳兼容"""
    return ""


# ══════════════════════════════════════════════════════════════
# 流动性健康度（V3 新增）
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

        turnover = (vol / mcap * 100) if mcap > 0 else None
        bs_ratio = buyers / max(sellers, 1)

        # 量价背离检测 (V5: 扩展为4种)
        vpd = ""
        if abs(price_chg) <= 3 and vol_change >= 50:
            vpd = "潜伏吸筹"
        elif price_chg >= 5 and vol_change >= 50:
            vpd = "突破放量"
        elif price_chg > 3 and vol_change < -30:
            vpd = "价涨量缩"
        elif price_chg < -3 and vol_change > 30:
            vpd = "价跌量增"

        # 评级 (V5.1 精细化: 拆分具体量价背离类型)
        if vol < 1000 or (avg7_vol > 0 and vol < avg7_vol * 0.3 and vol < 100000):
            grade = "❌枯竭"
        elif reserve < 10000:
            grade = "⚠低LP"
        elif vpd:
            vpd_grade_map = {
                "潜伏吸筹": "🔍潜伏",
                "突破放量": "🚀突破",
                "价涨量缩": "⚠量缩",
                "价跌量增": "⚠量增"
            }
            grade = vpd_grade_map.get(vpd, "⚠背离")
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
    def _fmtk(v):
        if v is None: return "—"
        if v >= 1e6: return f"${v/1e6:.1f}M"
        if v >= 1e3: return f"${v/1e3:.0f}K"
        return f"${v:.0f}"
    for r in results[:25]:
        lines.append(
            f"| {r['symbol']} | {_fmtk(r['vol_24h'])} | {_fmtk(r['avg7_vol'])} "
            f"| {r['vol_change']:+.0f}% | {_fmtk(r['reserve'])} | {f'{r["turnover"]:.1f}%' if r['turnover'] is not None else '—'} "
            f"| {r['buy_pct']:.0f}% | {r['bs_ratio']:.1f}x | {r['grade']} |"
        )

    # 量价背离
    vpd_list = [r for r in results if r["vpd"]]
    if vpd_list:
        lines += ["", "### ⚠ 量价背离", "",
                  "| 代币 | 24h价格 | 量变化 | 类型 | 含义 |",
                  "|------|---------|--------|------|------|"]
        for r in vpd_list:
            vpd_map = {"潜伏吸筹": "主力暗中吃单", "突破放量": "强势拉升", "价涨量缩": "拉盘无力", "价跌量增": "恐慌抛售"}
            meaning = vpd_map.get(r["vpd"], r["vpd"])
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
# 信号质量评估（V3: 替代旧模块4）
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
    other_items = []
    for combo, items in sorted_combos:
        rnow = [r["ret_now"] for r in items if r["ret_now"] is not None]
        mdds = [r["mdd"] for r in items if r["mdd"] is not None]
        if not rnow:
            continue
        if len(items) < 3:
            other_items.extend(items)
            continue
        wr = f"{sum(1 for x in rnow if x > 0) / len(rnow) * 100:.0f}%"
        avg_r = f"{sum(rnow) / len(rnow):+.1f}%"
        med_r = f"{statistics.median(rnow):+.1f}%"
        med_mdd = f"{statistics.median(mdds):+.1f}%" if mdds else "—"
        lines.append(f"| {combo} | {len(items)} | {wr} | {avg_r} | {med_r} | {med_mdd} |")
    # 折叠小样本
    if other_items:
        rnow = [r["ret_now"] for r in other_items if r["ret_now"] is not None]
        mdds = [r["mdd"] for r in other_items if r["mdd"] is not None]
        if rnow:
            wr = f"{sum(1 for x in rnow if x > 0) / len(rnow) * 100:.0f}%"
            avg_r = f"{sum(rnow) / len(rnow):+.1f}%"
            med_r = f"{statistics.median(rnow):+.1f}%"
            med_mdd = f"{statistics.median(mdds):+.1f}%" if mdds else "—"
            lines.append(f"| 其他(N<3) | {len(other_items)} | {wr} | {avg_r} | {med_r} | {med_mdd} |")

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

    # --- V4.0: 按积分分桶（原模块4合入） ---
    lines += ["", "### 按综合分分桶", "",
              "| 积分区间 | 样本 | 至今胜率 | 至今均收 | 至今中位 | 备注 |",
              "|----------|------|---------|---------|---------|------|"]
    buckets = [
        ("≥7", lambda r: r.get("meta_score", 0) >= 7),
        ("5-7", lambda r: 5 <= r.get("meta_score", 0) < 7),
        ("3-5", lambda r: 3 <= r.get("meta_score", 0) < 5),
        ("0-3", lambda r: 0 <= r.get("meta_score", 0) < 3),
        ("<0", lambda r: r.get("meta_score", 0) < 0),
    ]
    for name, pred in buckets:
        grp = [r for r in merged if pred(r)]
        if not grp:
            continue
        rnow = [r["ret_now"] for r in grp if r["ret_now"] is not None]
        if not rnow:
            continue
        wr = f"{sum(1 for x in rnow if x > 0) / len(rnow) * 100:.0f}%"
        avg_r = f"{sum(rnow) / len(rnow):+.1f}%"
        med_r = f"{statistics.median(rnow):+.1f}%"
        note = "⚠小样本" if len(grp) < 10 else ""
        lines.append(f"| {name} | {len(grp)} | {wr} | {avg_r} | {med_r} | {note} |")

    # --- V4.0: 按引擎数分桶 ---
    lines += ["", "### 按引擎数分桶", "",
              "| 引擎数 | 样本 | 至今胜率 | 至今均收 | 至今中位 | 备注 |",
              "|--------|------|---------|---------|---------|------|"]
    for name, lo, hi in [("≥4", 4, 99), ("3", 3, 3), ("2", 2, 2), ("1", 1, 1)]:
        grp = [r for r in merged if lo <= r.get("engines", 0) <= hi]
        if not grp:
            continue
        rnow = [r["ret_now"] for r in grp if r["ret_now"] is not None]
        if not rnow:
            continue
        wr = f"{sum(1 for x in rnow if x > 0) / len(rnow) * 100:.0f}%"
        avg_r = f"{sum(rnow) / len(rnow):+.1f}%"
        med_r = f"{statistics.median(rnow):+.1f}%"
        note = "⚠小样本" if len(grp) < 10 else ""
        lines.append(f"| {name} | {len(grp)} | {wr} | {avg_r} | {med_r} | {note} |")

    lines.append("")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# token_history 持久化
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


def calc_pnl_ratio(sumdb, addr):
    """V5: 从 cost_basis_snapshots 计算庄家浮盈率"""
    try:
        row = sumdb.execute(
            "SELECT vwap, gecko_price FROM cost_basis_snapshots "
            "WHERE token_address=? ORDER BY scan_time DESC LIMIT 1", (addr,)
        ).fetchone()
        if not row or not row["vwap"] or row["vwap"] <= 0:
            return None
        return (row["gecko_price"] - row["vwap"]) / row["vwap"] * 100
    except Exception:
        return None


def evaluate_whale_divergence(price_chg_24h, top10_delta_24h):
    """V5: 判定诱多出货背离 (价格涨+大户撤)"""
    if price_chg_24h is not None and top10_delta_24h is not None:
        if price_chg_24h > 5.0 and top10_delta_24h < -1.0:
            return 1
    return 0


def load_double_track_metrics(src):
    """从 select.db 批量读取最新快照的浓度、大盘分、精英分"""
    rows = src.execute("""
        WITH latest AS (
            SELECT token_address, MAX(snapshot_time) as mx
            FROM bubblemap_holders GROUP BY token_address
        )
        SELECT bh.token_address,
            SUM(CASE WHEN bh.is_cex=0 AND bh.is_dex=0 AND bh.is_contract=0 THEN 1 ELSE 0 END) as real_user_count,
            SUM(CASE WHEN bh.is_accumulating=1 THEN 1 ELSE 0 END) as acc_count,
            AVG(CASE WHEN bh.is_accumulating=1 THEN bh.acc_score ELSE NULL END) as micro_score,
            AVG(CASE WHEN bh.is_cex=0 AND bh.is_dex=0 AND bh.is_contract=0
                      THEN bh.acc_score ELSE NULL END) as macro_score
        FROM bubblemap_holders bh
        JOIN latest l ON bh.token_address=l.token_address AND bh.snapshot_time=l.mx
        GROUP BY bh.token_address
    """).fetchall()
    result = {}
    for r in rows:
        real = r["real_user_count"] or 0
        acc = r["acc_count"] or 0
        result[r["token_address"].lower()] = {
            "concentration": round(acc / max(real, 1) * 100, 1),
            "macro_score": round(r["macro_score"] or 0, 1),
            "micro_score": round(r["micro_score"] or 0, 1),
        }
    return result


def save_token_history(sumdb, bt_data, mig_data, ts_data, liq_data=None, dt_map=None):
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

    # V5: 多时间尺度 + 浮盈率 + 出货背离
    for col, typedef in [("retention_24h", "REAL"), ("retention_72h", "REAL"),
                          ("top10_delta_24h", "REAL"), ("top10_delta_72h", "REAL"),
                          ("pnl_ratio", "REAL"), ("bs_ratio_24h", "REAL"),
                          ("whale_divergence", "INTEGER DEFAULT 0")]:
        try:
            sumdb.execute(f"ALTER TABLE token_history ADD COLUMN {col} {typedef}")
        except Exception:
            pass

    # V6: 双轨制评分+浓度 (DDL防御)
    for col, typedef in [("concentration", "REAL"), ("macro_score", "REAL"), ("micro_score", "REAL")]:
        try:
            sumdb.execute(f"ALTER TABLE token_history ADD COLUMN {col} {typedef}")
        except Exception:
            pass

    # V7: 新增时序对齐与超额收益重构字段
    for col, typedef in [("rolling_7d_ret", "REAL"), ("alpha_ret", "REAL"),
                          ("peak_return", "REAL"), ("resilience_index", "REAL")]:
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

    def _insert(addr, sym, bt, mig, ts, liq, dt=None):
        nonlocal count
        try:
            rolling = bt.get("rolling_7d_ret") if bt else None
            ret_72h = mig.get("retention_72h") if mig else None
            resilience = None
            if rolling is not None and ret_72h is not None:
                resilience = rolling / max((1.0 - ret_72h / 100.0), 0.01)

            sumdb.execute("""
                INSERT OR REPLACE INTO token_history
                (computed_date, token_address, token_symbol, signal_first_seen,
                 signal_level, entry_price, price_7d_ret, price_14d_ret, price_now_ret,
                 retention_7d, retention_14d, whale_entered, whale_exited,
                 top10_pct, top10_delta, acc_count, acc_delta,
                 score_slope, score_sigma, consec_acc,
                 volume_24h, reserve_usd, buy_tx_pct, turnover_ratio, mdd,
                 retention_24h, retention_72h, top10_delta_24h, top10_delta_72h,
                 pnl_ratio, bs_ratio_24h, whale_divergence, concentration, macro_score, micro_score,
                 rolling_7d_ret, alpha_ret, peak_return, resilience_index)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                mig.get("retention_24h"), mig.get("retention_72h"),
                mig.get("top10_delta_24h"), mig.get("top10_delta_72h"),
                calc_pnl_ratio(sumdb, addr),
                liq.get("bs_ratio", 1.0),
                evaluate_whale_divergence(liq.get("price_chg"), mig.get("top10_delta_24h")),
                dt.get("concentration") if dt else None,
                dt.get("macro_score") if dt else None,
                dt.get("micro_score") if dt else None,
                rolling,
                bt.get("alpha_ret") if bt else None,
                bt.get("peak_return") if bt else None,
                resilience,
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
        dt = dt_map.get(addr.lower()) if (dt_map and addr) else None
        _insert(addr, sym, bt, mig_map.get(addr, {}), ts_map.get(sym, {}), liq_map.get(sym, {}), dt)

    # 2. 写入有迁移数据但无回测数据的代币
    for mig in mig_data:
        addr = mig.get("addr", "")
        if addr in written_addrs or not mig.get("symbol"):
            continue
        sym = mig["symbol"]
        dt = dt_map.get(addr.lower()) if (dt_map and addr) else None
        _insert(addr, sym, None, mig, ts_map.get(sym, {}), liq_map.get(sym, {}), dt)

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
            dt = dt_map.get(addr.lower()) if (dt_map and addr) else None
            _insert(addr, sym, None, {}, ts_map.get(sym, {}), liq_map.get(sym, {}), dt)
    except Exception:
        pass

    sumdb.commit()
    return count


# ══════════════════════════════════════════════════════════════
# 单币画像（Top ACC 代币完整档案）
# ══════════════════════════════════════════════════════════════
def coin_profile(bt_data, mig_data, ts_data, sumdb, liq_data=None, dt_map=None):
    """V4.0: Top ACC 表格看板（精简版，排除遗漏检测标记的代币）"""
    meta_map = {}
    try:
        for r in sumdb.execute(
            "SELECT token_symbol, meta_score, meta_verdict, engine_hits, stage "
            "FROM meta_snapshots WHERE scan_time=(SELECT MAX(scan_time) FROM meta_snapshots)"
        ):
            meta_map[r["token_symbol"]] = dict(r)
    except Exception:
        pass

    # A2: 排除被遗漏检测标记的代币（留存<50% + Top10下降）
    mig_sym_map = {r["symbol"]: r for r in mig_data if r.get("symbol")}
    excluded = set()
    for sym, mig in mig_sym_map.items():
        retention = mig.get("retention_7d", 100)
        top10_delta = mig.get("top10_delta_7d", 0)
        if retention < 50 and top10_delta < 0:
            excluded.add(sym)

    # V5: sym → addr 映射 (用于 pnl_ratio 查询)
    sym_addr = {}
    try:
        for _r in sumdb.execute("SELECT token_symbol, token_address FROM watchlist"):
            sym_addr[_r["token_symbol"]] = _r["token_address"]
    except Exception:
        pass

    acc_tokens = [(sym, info) for sym, info in meta_map.items()
                  if info.get("meta_verdict") == "ACC" and sym not in excluded]
    acc_tokens.sort(key=lambda x: x[1].get("meta_score", 0), reverse=True)

    if not acc_tokens:
        return ""

    ts_map = {r["symbol"]: r for r in ts_data}
    bt_map = {r["symbol"]: r for r in bt_data}
    liq_map = {r["symbol"]: r for r in (liq_data or [])}

    def _fk(v):
        if v is None: return "—"
        if v >= 1e6: return f"${v/1e6:.1f}M"
        if v >= 1e3: return f"${v/1e3:.0f}K"
        return f"${v:.0f}"

    lines = ["## 🎯 Top ACC 综合看板", ""]
    if excluded:
        lines.append(f"> 已排除遗漏检测标记代币: {', '.join(sorted(excluded))}")
        lines.append("")
    lines += [
        "| 代币 | 分数 | 浓度 | 大盘/精英 | 阶段 | 至今收益 | MDD | 24h留存 | 7d留存 | 24h量 | 浮盈率 | 庄出逃 | 评级 |",
        "|------|------|------|-----------|------|---------|-----|--------|--------|-------|--------|--------|------|",
    ]

    stage_map = {"CONTROLLED": "CTRL", "ACCUMULATING": "ACC", "DISTRIBUTING": "DIST",
                 "WATCHLIST": "WATCH", "NEUTRAL": "—"}

    for sym, meta in acc_tokens[:15]:
        mig = mig_sym_map.get(sym, {})
        ts = ts_map.get(sym, {})
        bt = bt_map.get(sym, {})
        liq = liq_map.get(sym, {})

        score = f"{meta.get('meta_score', 0):.1f}"
        
        # 兜底获取 token 实际地址以查询 dt_map
        token_addr = sym_addr.get(sym, "")
        if not token_addr:
            for bt_item in bt_data:
                if bt_item["symbol"] == sym:
                    token_addr = bt_item.get("addr", "")
                    break
        if not token_addr:
            for mig_item in mig_data:
                if mig_item["symbol"] == sym:
                    token_addr = mig_item.get("addr", "")
                    break
        
        addr_lower = token_addr.lower() if token_addr else ""
        dt = dt_map.get(addr_lower) if (dt_map and addr_lower) else None
        conc_str = f"{dt['concentration']:.1f}%" if (dt and dt.get('concentration') is not None) else "—"
        track_str = f"{dt['macro_score']:.1f}/{dt['micro_score']:.1f}" if (dt and dt.get('macro_score') is not None) else "—"

        stage = stage_map.get(meta.get("stage", ""), "—")
        ret_now = f"{bt['ret_now']:+.1f}%" if bt.get("ret_now") is not None else "—"
        mdd = f"{bt['mdd']:+.1f}%" if bt.get("mdd") is not None else "—"
        retention = f"{mig.get('retention_7d', 0):.0f}%" if mig.get("retention_7d") is not None else "—"
        vol = _fk(liq.get("vol_24h"))
        lp = _fk(liq.get("reserve"))
        grade = liq.get("grade", "—")

        ret_24h = f"{mig.get('retention_24h', 0):.0f}%" if mig.get("retention_24h") is not None else "—"
        pnl = calc_pnl_ratio(sumdb, token_addr)
        pnl_str = f"{pnl:+.0f}%" if pnl is not None else "—"
        wd = evaluate_whale_divergence(liq.get("price_chg"), mig.get("top10_delta_24h"))
        wd_str = "🚨" if wd else "—"
        
        lines.append(
            f"| {sym} | {score} | {conc_str} | {track_str} | {stage} | {ret_now} | {mdd} "
            f"| {ret_24h} | {retention} | {vol} | {pnl_str} | {wd_str} | {grade} |"
        )

    lines.append("")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# 大户行为变化追踪（首次减仓 + DORMANT + 成本信念度）
# ══════════════════════════════════════════════════════════════

def whale_behavior_alert(src, sumdb):
    """地址级行为追踪：首次减仓预警 + DORMANT锁仓统计 + 成本信念度交叉"""
    # ── 获取 ACC 代币列表 ──
    acc_tokens = {}
    try:
        for r in sumdb.execute(
            "SELECT token_symbol, token_address FROM watchlist "
            "WHERE token_address IN ("
            "  SELECT DISTINCT token_address FROM meta_snapshots "
            "  WHERE meta_verdict='ACC' AND scan_time >= datetime('now','-7 days')"
            ")"
        ):
            acc_tokens[r["token_address"]] = r["token_symbol"]
    except Exception:
        return ""

    if not acc_tokens:
        return ""

    # ── 获取 cost_basis 数据 ──
    cb_map = {}  # token_address -> {underwater_pct, vwap, gecko_price}
    try:
        for r in sumdb.execute(
            "SELECT token_address, underwater_pct, vwap, gecko_price "
            "FROM cost_basis_snapshots "
            "WHERE scan_time = (SELECT MAX(scan_time) FROM cost_basis_snapshots)"
        ):
            cb_map[r["token_address"]] = {
                "underwater_pct": r["underwater_pct"] or 0,
                "vwap": r["vwap"] or 0,
                "gecko_price": r["gecko_price"] or 0,
            }
    except Exception:
        pass

    amber_alerts = []   # 首次减仓
    dormant_stats = []  # DORMANT 统计
    STABLE_THRESHOLD = 0.01  # hold_amount 变化 < 1% 视为稳定
    MIN_STABLE_SNAPS = 3     # 至少连续 3 个稳定快照才算 DORMANT

    for addr, sym in acc_tokens.items():
        # ── 取最近 6 个快照 ──
        snaps = src.execute(
            "SELECT DISTINCT snapshot_time FROM bubblemap_holders "
            "WHERE token_address = ? ORDER BY snapshot_time DESC LIMIT 6",
            (addr,)
        ).fetchall()
        if len(snaps) < 2:
            continue

        latest_snap = snaps[0][0]
        prev_snap = snaps[1][0]

        # ── 取当前和上一快照的吸筹地址 ──
        cur_rows = src.execute(
            "SELECT wallet_address, hold_amount, acc_score, buy_cnt, sell_cnt, "
            "       dex_ratio, hold_percentage "
            "FROM bubblemap_holders "
            "WHERE token_address = ? AND snapshot_time = ? AND is_accumulating = 1",
            (addr, latest_snap)
        ).fetchall()
        prev_rows = src.execute(
            "SELECT wallet_address, hold_amount "
            "FROM bubblemap_holders "
            "WHERE token_address = ? AND snapshot_time = ? AND is_accumulating = 1",
            (addr, prev_snap)
        ).fetchall()

        if not cur_rows:
            continue

        prev_map = {r["wallet_address"]: r["hold_amount"] for r in prev_rows}
        acc_count = len(cur_rows)
        dormant_count = 0
        total_stable_snaps = 0

        for row in cur_rows:
            waddr = row["wallet_address"]
            cur_hold = row["hold_amount"] or 0

            # ── 回溯连续稳定快照数 ──
            stable_count = 0
            prev_hold_check = cur_hold
            for i in range(1, min(len(snaps), 6)):
                snap_i = snaps[i][0]
                hist = src.execute(
                    "SELECT hold_amount FROM bubblemap_holders "
                    "WHERE token_address = ? AND snapshot_time = ? "
                    "AND wallet_address = ? AND is_accumulating = 1",
                    (addr, snap_i, waddr)
                ).fetchone()
                if not hist or not hist["hold_amount"]:
                    break
                hist_hold = hist["hold_amount"]
                if hist_hold > 0 and abs(prev_hold_check - hist_hold) / hist_hold < STABLE_THRESHOLD:
                    stable_count += 1
                    prev_hold_check = hist_hold
                else:
                    break

            if stable_count >= MIN_STABLE_SNAPS:
                dormant_count += 1
                total_stable_snaps += stable_count

            # ── 首次减仓检测 ──
            if waddr in prev_map and prev_map[waddr] > 0:
                prev_hold = prev_map[waddr]
                change_pct = (cur_hold - prev_hold) / prev_hold
                if change_pct < -0.01 and stable_count >= MIN_STABLE_SNAPS:
                    # DORMANT 地址首次减仓 → AMBER
                    amber_alerts.append({
                        "symbol": sym,
                        "wallet": waddr,
                        "prev_hold": prev_hold,
                        "cur_hold": cur_hold,
                        "change_pct": change_pct * 100,
                        "stable_snaps": stable_count,
                        "acc_score": row["acc_score"] or 0,
                        "level": "🔴RED" if change_pct < -0.30 else "🟠AMBER",
                    })

        # ── DORMANT 统计 ──
        cb = cb_map.get(addr, {})
        underwater = cb.get("underwater_pct", 0)
        vwap = cb.get("vwap", 0)
        price = cb.get("gecko_price", 0)

        # 信念评级
        dormant_rate = dormant_count / max(acc_count, 1)
        avg_stable = total_stable_snaps / max(dormant_count, 1)
        if dormant_rate >= 0.8 and underwater >= 80:
            conviction = "💎套牢不割"
        elif dormant_rate >= 0.7:
            conviction = "🔒强锁仓"
        elif dormant_rate >= 0.5:
            conviction = "✅中等锁仓"
        else:
            conviction = "⚠️流动"

        dormant_stats.append({
            "symbol": sym,
            "acc_count": acc_count,
            "dormant_count": dormant_count,
            "dormant_rate": dormant_rate * 100,
            "avg_stable": avg_stable,
            "underwater": underwater,
            "vwap": vwap,
            "price": price,
            "conviction": conviction,
        })

    # ── 组装 Markdown ──
    lines = ["## 🐋 大户行为变化追踪", ""]

    # 子模块 A: 首次减仓预警
    if amber_alerts:
        amber_alerts.sort(key=lambda x: x["change_pct"])
        lines += [
            "### 🚨 首次减仓预警（AMBER）",
            "",
            "> DORMANT 地址（连续≥3快照持仓不变）首次出现减仓",
            "",
            "| 代币 | 地址 | 前次持仓 | 当前持仓 | 变化% | 稳定快照 | 分数 | 级别 |",
            "|------|------|---------|---------|-------|---------|------|------|",
        ]
        for a in amber_alerts[:15]:
            lines.append(
                f"| {a['symbol']} | `{a['wallet'][:8]}..{a['wallet'][-4:]}` "
                f"| {a['prev_hold']:,.0f} | {a['cur_hold']:,.0f} "
                f"| {a['change_pct']:+.1f}% | {a['stable_snaps']} "
                f"| {a['acc_score']:.0f} | {a['level']} |"
            )
        lines.append("")
    else:
        lines += ["### ✅ 无首次减仓预警", "",
                   "> 所有 DORMANT 吸筹地址持仓稳定，无异常减仓", ""]

    # 子模块 B: DORMANT 锁仓统计
    dormant_stats.sort(key=lambda x: x["dormant_rate"], reverse=True)
    high_conviction = [d for d in dormant_stats if d["dormant_rate"] >= 50]
    if high_conviction:
        lines += [
            "### 🔒 锁仓休眠分析（DORMANT）",
            "",
            "| 代币 | 吸筹数 | DORMANT | 休眠率% | 平均稳定 | 水下% | VWAP/价格 | 信念 |",
            "|------|--------|---------|---------|---------|-------|----------|------|",
        ]
        for d in high_conviction[:20]:
            vwap_ratio = f"{d['vwap']/d['price']:.1f}x" if d["price"] > 0 and d["vwap"] > 0 else "—"
            lines.append(
                f"| {d['symbol']} | {d['acc_count']} | {d['dormant_count']} "
                f"| {d['dormant_rate']:.0f}% | {d['avg_stable']:.1f} "
                f"| {d['underwater']:.0f}% | {vwap_ratio} | {d['conviction']} |"
            )
        lines.append("")

    lines.append("")
    return "\n".join(lines)


def hop2_analysis(src, sumdb):
    """Hop2 隐蔽吸筹置信度 analysis（独立统计维度）"""
    tokens = sumdb.execute("""
        SELECT token_address, token_symbol, signal_level
        FROM watchlist WHERE signal_level IN ('DIAMOND','RED','YELLOW')
    """).fetchall()

    if not tokens:
        return ""

    stats = []
    # 全局累计
    total_with_hop2 = 0
    total_tokens = len(tokens)

    for t in tokens:
        addr = t["token_address"]
        sym = t["token_symbol"] or "?"
        sig = t["signal_level"]

        row = src.execute("""
            SELECT
                COUNT(*)                                                              AS total_holders,
                COALESCE(SUM(is_accumulating), 0)                                     AS acc_count,
                SUM(CASE WHEN dex_ratio_hop2 IS NOT NULL AND dex_ratio_hop2 >= 0.5
                         THEN 1 ELSE 0 END)                                           AS hop2_high,
                SUM(CASE WHEN is_accumulating = 1
                         AND dex_ratio_hop2 IS NOT NULL AND dex_ratio_hop2 >= 0.5
                         THEN 1 ELSE 0 END)                                           AS hop2_acc,
                SUM(CASE WHEN entity_id IS NOT NULL AND entity_id != ''
                         THEN 1 ELSE 0 END)                                           AS entity_cnt,
                COUNT(DISTINCT CASE WHEN entity_id IS NOT NULL AND entity_id != ''
                              THEN entity_id END)                                      AS uniq_ent,
                SUM(CASE WHEN gmgn_verified = 2 AND dex_ratio_hop2 >= 0.5
                         THEN 1 ELSE 0 END)                                           AS t98,
                SUM(CASE WHEN gmgn_verified = 1 AND dex_ratio_hop2 >= 0.5
                         THEN 1 ELSE 0 END)                                           AS t90,
                SUM(CASE WHEN gmgn_verified = 0 AND dex_ratio_hop2 >= 0.5
                         THEN 1 ELSE 0 END)                                           AS t30,
                SUM(CASE WHEN gmgn_verified IS NULL AND dex_ratio_hop2 >= 0.5
                         THEN 1 ELSE 0 END)                                           AS t80,
                AVG(CASE WHEN is_accumulating = 1 THEN dex_ratio_hop2 END)            AS hop2_avg
            FROM bubblemap_holders
            WHERE token_address = ?
              AND batch_id = (
                  SELECT MAX(batch_id) FROM bubblemap_holders WHERE token_address = ?
              )
        """, [addr, addr]).fetchone()

        if not row or (row["total_holders"] or 0) == 0:
            continue

        acc = row["acc_count"] or 0
        hop2_acc = row["hop2_acc"] or 0
        hop2_high = row["hop2_high"] or 0
        hop2_pct = hop2_acc / max(acc, 1)
        ent_rate = (row["entity_cnt"] or 0) / max(row["total_holders"], 1)

        if hop2_high > 0:
            total_with_hop2 += 1

        # 关联回测收益
        ret_7d = None
        ret_14d = None
        first = sumdb.execute("""
            SELECT scan_time FROM meta_snapshots
            WHERE token_address = ? AND meta_verdict = 'ACC'
            ORDER BY scan_time LIMIT 1
        """, [addr]).fetchone()
        if first:
            sig_date = first[0][:19]
            try:
                from datetime import datetime as _dt2, timedelta as _td
                sd = _dt2.strptime(sig_date, "%Y-%m-%d %H:%M:%S")
                p0 = src.execute(
                    "SELECT price_usd FROM gecko_market_data "
                    "WHERE token_address=? AND scan_time>=? AND price_usd>0 "
                    "ORDER BY scan_time LIMIT 1", (addr, sig_date)
                ).fetchone()
                if p0 and p0[0] > 0:
                    entry = p0[0]
                    d7 = (sd + _td(days=7)).strftime("%Y-%m-%d %H:%M:%S")
                    p7 = src.execute(
                        "SELECT price_usd FROM gecko_market_data "
                        "WHERE token_address=? AND scan_time>=? AND price_usd>0 "
                        "ORDER BY scan_time LIMIT 1", (addr, d7)
                    ).fetchone()
                    if p7:
                        ret_7d = (p7[0] - entry) / entry * 100
                    d14 = (sd + _td(days=14)).strftime("%Y-%m-%d %H:%M:%S")
                    p14 = src.execute(
                        "SELECT price_usd FROM gecko_market_data "
                        "WHERE token_address=? AND scan_time>=? AND price_usd>0 "
                        "ORDER BY scan_time LIMIT 1", (addr, d14)
                    ).fetchone()
                    if p14:
                        ret_14d = (p14[0] - entry) / entry * 100
            except Exception:
                pass

        stats.append({
            "symbol": sym, "signal": sig,
            "total": row["total_holders"], "acc": acc,
            "hop2_high": hop2_high, "hop2_acc": hop2_acc,
            "hop2_pct": hop2_pct, "ent_rate": ent_rate,
            "t98": row["t98"] or 0, "t90": row["t90"] or 0,
            "t30": row["t30"] or 0, "t80": row["t80"] or 0,
            "hop2_avg": row["hop2_avg"] or 0,
            "uniq_ent": row["uniq_ent"] or 0,
            "ret_7d": ret_7d, "ret_14d": ret_14d,
        })

    if not stats:
        return ""

    # ── 分组对比 ──
    high_grp = [s for s in stats if s["hop2_pct"] >= 0.3]
    low_grp  = [s for s in stats if s["hop2_pct"] < 0.3]

    def _win(lst, key):
        valid = [s for s in lst if s[key] is not None]
        if not valid:
            return "—"
        w = sum(1 for s in valid if s[key] > 0)
        return f"{w}/{len(valid)} ({w/len(valid)*100:.0f}%)"

    def _avg(lst, key):
        valid = [s[key] for s in lst if s[key] is not None]
        if not valid:
            return "—"
        return f"{sum(valid)/len(valid):+.1f}%"

    # 置信度分档汇总
    sum_t98 = sum(s["t98"] for s in stats)
    sum_t90 = sum(s["t90"] for s in stats)
    sum_t30 = sum(s["t30"] for s in stats)
    sum_t80 = sum(s["t80"] for s in stats)
    tier_total = max(sum_t98 + sum_t90 + sum_t30 + sum_t80, 1)

    hop2_coverage = total_with_hop2 / max(total_tokens, 1)
    avg_hop2_pct = sum(s["hop2_pct"] for s in stats) / len(stats) if stats else 0
    avg_ent_rate = sum(s["ent_rate"] for s in stats) / len(stats) if stats else 0

    md = "## 🔬 Hop2 隐蔽吸筹置信度分析\n\n"

    md += "### 全局统计\n\n"
    md += "| 指标 | 值 |\n|------|-----|\n"
    md += f"| watchlist 代币数 | {total_tokens} |\n"
    md += f"| hop2 覆盖率 | {total_with_hop2}/{total_tokens} ({hop2_coverage:.0%}) |\n"
    md += f"| 吸筹者 hop2 高占比(均) | {avg_hop2_pct:.1%} |\n"
    md += f"| entity 穿透率(均) | {avg_ent_rate:.1%} |\n\n"

    md += "### hop2 高组 vs 低组\n\n"
    md += f"| 指标 | 高组(≥30%, {len(high_grp)}) | 低组(<30%, {len(low_grp)}) |\n"
    md += f"|------|---------|--------|\n"
    md += f"| 7d 胜率 | {_win(high_grp, 'ret_7d')} | {_win(low_grp, 'ret_7d')} |\n"
    md += f"| 14d 胜率 | {_win(high_grp, 'ret_14d')} | {_win(low_grp, 'ret_14d')} |\n"
    md += f"| 7d 均收益 | {_avg(high_grp, 'ret_7d')} | {_avg(low_grp, 'ret_7d')} |\n"
    md += f"| 14d 均收益 | {_avg(high_grp, 'ret_14d')} | {_avg(low_grp, 'ret_14d')} |\n\n"

    md += "### 置信度分档分布\n\n"
    md += "| 档位 | 数量 | 占比 |\n|------|------|------|\n"
    md += f"| 98分(GMGN双确认) | {sum_t98} | {sum_t98/tier_total:.0%} |\n"
    md += f"| 90分(GMGN单确认) | {sum_t90} | {sum_t90/tier_total:.0%} |\n"
    md += f"| 80分(未验证)     | {sum_t80} | {sum_t80/tier_total:.0%} |\n"
    md += f"| 30分(GMGN否决)   | {sum_t30} | {sum_t30/tier_total:.0%} |\n\n"

    # Top 10 hop2 代币
    top10 = sorted(stats, key=lambda s: s["hop2_pct"], reverse=True)[:10]
    if top10:
        prev_rank_map = {}
        try:
            times = sumdb.execute(
                "SELECT DISTINCT scan_time FROM hop2_tracking ORDER BY scan_time DESC LIMIT 2"
            ).fetchall()
            if len(times) >= 2:
                prev_time = times[1]["scan_time"]
                prev_rows = sumdb.execute(
                    "SELECT token_symbol, hop2_acc_pct FROM hop2_tracking "
                    "WHERE scan_time = ? AND hop2_acc_pct > 0 ORDER BY hop2_acc_pct DESC",
                    [prev_time]
                ).fetchall()
                for idx, r in enumerate(prev_rows):
                    prev_rank_map[r["token_symbol"]] = idx + 1
        except Exception:
            pass

        md += "### Top 10 hop2 代币\n\n"
        md += "| # | 趋势 | 代币 | 信号 | hop2占比 | entity数 | 7d | 14d |\n"
        md += "|---|------|------|------|----------|----------|-----|------|\n"
        for idx, s in enumerate(top10):
            rank = idx + 1
            r7 = f"{s['ret_7d']:+.1f}%" if s['ret_7d'] is not None else "—"
            r14 = f"{s['ret_14d']:+.1f}%" if s['ret_14d'] is not None else "—"
            sym = s['symbol']
            if sym in prev_rank_map:
                change = prev_rank_map[sym] - rank
                if change > 0:
                    trend = f"↑{change}"
                elif change < 0:
                    trend = f"↓{abs(change)}"
                else:
                    trend = "─"
            else:
                trend = "🆕"
            pct_mark = ""
            if s['hop2_pct'] >= 0.5:
                pct_mark = " 🟡"
            elif s['hop2_pct'] >= 0.15:
                pct_mark = " 🟣"
            md += f"| {rank} | {trend} | {sym} | {s['signal']} | {s['hop2_pct']:.0%}{pct_mark} | {s['uniq_ent']} | {r7} | {r14} |\n"

    return md


def main():
    import time
    t0 = time.time()
    print(f"AI-SUM 长期分析报告 V5.0 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    src = connect(SRC_DB, readonly=True)
    sumdb = connect(SUM_DB)

    # P-DB: 确保表存在
    ensure_token_history_table(sumdb)

    # 模块 1: 信号回测
    print("  [1/7] 信号回测（1d/3d/7d/14d + MDD）...")
    bt_md, bt_data = backtest_watchlist(src, sumdb)
    print(f"        {len(bt_data)} 个代币有回测数据")

    # 模块 2: holder 迁移
    print("  [2/7] holder 迁移（14d + 遗漏检测）...")
    mig_md, mig_data = migration_analysis(src, sumdb)
    print(f"        {len(mig_data)} 个代币有迁移数据")

    # 模块 2.5: 大户行为追踪
    print("  [2.5/7] 大户行为追踪...")
    whale_md = whale_behavior_alert(src, sumdb)
    if whale_md:
        print("          大户行为分析完成")

    # 模块 3: 积分时序
    print("  [3/7] 积分时序...")
    ts_md, ts_data = score_timeseries(sumdb)
    print(f"        {len(ts_data)} 个代币有时序数据")

    # 模块 4: 流动性健康度
    print("  [4/7] 流动性健康度...")
    liq_md, liq_data = liquidity_health(src, sumdb)
    print(f"        {len(liq_data)} 个ACC代币有流动性数据")

    # 模块 5: 信号质量
    print("  [5/7] 信号质量评估...")
    quality_md = signal_quality(bt_data, sumdb)

    # 双轨制与浓度指标动态加载
    print("  [加载双轨指标] 获取最新浓度与大盘/精英分...")
    try:
        dt_map = load_double_track_metrics(src)
        print(f"        成功加载 {len(dt_map)} 个代币的双轨制指标")
    except Exception as e:
        print(f"        双轨指标加载失败: {e}")
        dt_map = {}

    # 模块 6: Top ACC 综合看板
    print("  [6/7] ACC综合看板...")
    profile_md = coin_profile(bt_data, mig_data, ts_data, sumdb, liq_data, dt_map=dt_map)

    # P-DB: 写入 token_history
    saved = save_token_history(sumdb, bt_data, mig_data, ts_data, liq_data, dt_map=dt_map)
    print(f"  [DB] token_history 写入 {saved} 行")

    # 组装报告
    now_str = datetime.now().strftime("%m-%d %H:%M")
    header = f"""# 📊 AI-SUM 长期分析报告 V5.0 — {now_str}

> 时间基准: 首次信号时间（meta_snapshots/unified_results）
> 数据源: bubblemap + gecko(20字段) + meta {len(ts_data)}代币
> 生成: history_report.py V5.0 | 耗时: {{elapsed:.1f}}s

---

"""
    # 模块 7: hop2 隐蔽吸筹分析 (v5)

    print("  [7/7] hop2 隐蔽吸筹分析...")
    hop2_md = hop2_analysis(src, sumdb)
    if hop2_md:
        print(f"        hop2 分析完成")

    parts = [bt_md, mig_md]
    if whale_md:
        parts.append(whale_md)
    parts.extend([ts_md, liq_md, quality_md])
    if hop2_md:
        parts.append(hop2_md)
    if profile_md:
        parts.append(profile_md)

    # 模块 8: 合约信号总览 (v6)
    print("  [8] 合约信号总览...")
    futures_md = futures_analysis(src, sumdb)
    if futures_md:
        parts.append(futures_md)
        print("        合约信号分析完成")
    body = "\n---\n\n".join(p for p in parts if p)
    elapsed = time.time() - t0
    md = header.format(elapsed=elapsed) + body
    md += f"\n\n---\n*生成时间: {datetime.now().isoformat()} | 耗时: {elapsed:.1f}s*\n"

    # 写入
    out_dir = Path(REPORT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"history_{datetime.now().strftime('%m%d_%H%M')}.md"
    path.write_text(md, encoding="utf-8")
    print(f"\n📄 报告: {path} ({len(md)} bytes, {elapsed:.1f}s)")

    src.close()
    sumdb.close()


# ── 模块 8: 合约信号总览 (v6 新增) ──
def futures_analysis(src, sumdb):
    """从 futures_snapshots 读取最新合约 data, 与 meta 交叉分析"""
    try:
        ft_rows = src.execute("""
            SELECT * FROM futures_snapshots
            WHERE scan_time = (SELECT MAX(scan_time) FROM futures_snapshots)
        """).fetchall()
    except Exception:
        return None

    if not ft_rows:
        return None

    # meta ACC 代币
    acc_tokens = {}
    try:
        for r in sumdb.execute("""
            SELECT token_address, token_symbol, meta_score, meta_verdict
            FROM meta_snapshots
            WHERE scan_time = (SELECT MAX(scan_time) FROM meta_snapshots)
              AND meta_verdict = 'ACC'
        """):
            acc_tokens[r[0].lower() if r[0] else ""] = {
                "symbol": r[1], "meta_score": r[2], "verdict": r[3]
            }
    except Exception:
        pass

    lines = ["## 📊 模块 8: 合约信号总览\n"]
    lines.append(f"> 合约数据代币: {len(ft_rows)} | ACC代币: {len(acc_tokens)}\n")

    # 8a: ACC 代币合约交叉验证
    if acc_tokens:
        lines.append("### ACC 代币合约验证\n")
        lines.append("| 代币 | meta | OI($) | OI变化 | FR | L/S | 验证 |")
        lines.append("|------|------|-------|--------|-----|-----|------|")
        for ft in ft_rows:
            addr = (ft["token_address"] or "").lower()
            if addr in acc_tokens:
                acc = acc_tokens[addr]
                oi_val = ft["oi_value_usd"] or 0
                oi_chg = ft["oi_change_24h"] or 0
                fr = ft["funding_rate"] or 0
                ls = ft["long_short_ratio"] or 0
                tag = "✅合约确认" if oi_chg > 0.05 else "⚠合约分歧" if oi_chg < -0.10 else "—平稳"
                lines.append(f"| {acc['symbol']} | {acc['meta_score']:.1f} | ${oi_val:,.0f} | {oi_chg:.1%} | {fr:.4%} | {ls:.2f} | {tag} |")
        lines.append("")

    # 8b: OI 变化 Top10
    sorted_oi = sorted(ft_rows, key=lambda x: abs(x["oi_change_24h"] or 0), reverse=True)[:10]
    lines.append("### OI 24h 变化 Top10\n")
    lines.append("| 代币 | OI($) | OI变化24h | FR | L/S |")
    lines.append("|------|-------|-----------|-----|-----|")
    for ft in sorted_oi:
        oi_val = ft["oi_value_usd"] or 0
        oi_chg = ft["oi_change_24h"] or 0
        fr = ft["funding_rate"] or 0
        ls = ft["long_short_ratio"] or 0
        arrow = "🔺" if oi_chg > 0 else "🔻"
        lines.append(f"| {ft['symbol']} | ${oi_val:,.0f} | {arrow} {oi_chg:.1%} | {fr:.4%} | {ls:.2f} |")
    lines.append("")

    # 8c: FR 极端值
    extreme_fr = [ft for ft in ft_rows if ft["funding_rate"] and abs(ft["funding_rate"]) > 0.0005]
    if extreme_fr:
        extreme_fr.sort(key=lambda x: x["funding_rate"])
        lines.append("### Funding Rate 极端值\n")
        lines.append("| 代币 | FR | 类型 | L/S | OI($) |")
        lines.append("|------|-----|------|-----|-------|")
        for ft in extreme_fr[:10]:
            fr = ft["funding_rate"]
            typ = "🔴空头拥挤" if fr < -0.0003 else "⚠偏空" if fr < 0 else "⚠多头过热" if fr > 0.001 else "偏多"
            ls = ft["long_short_ratio"] or 0
            lines.append(f"| {ft['symbol']} | {fr:.4%} | {typ} | {ls:.2f} | ${(ft['oi_value_usd'] or 0):,.0f} |")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
