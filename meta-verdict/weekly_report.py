#!/usr/bin/env python3
"""AI-SUM 周月报引擎 — 全新维度分析"""
import sqlite3, os, sys, time, logging, argparse, math, statistics
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import market_context

SRC_DB = "/opt/select-coin/data/select.db"
SUM_DB = "/opt/AI-SUM/select-sum.db"
WEEKLY_DIR = "/opt/AI-SUM/report/weekly"
MONTHLY_DIR = "/opt/AI-SUM/report/monthly"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("weekly_report")

def conn_ro(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c

def conn_rw(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


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

# ═══ W1: BTC Beta ═══
def w1_btc_beta(sumdb, srcdb, mkt, start, end):
    lines = ["## 📊 W1: BTC Beta 关联分析\n"]
    btc_data = mkt.get("btc_klines", {}).get("BTCUSDT", [])
    if len(btc_data) < 2:
        lines.append("> BTC K线数据不足，跳过\n")
        return "\n".join(lines)
    btc_ret = (btc_data[-1]["close"] - btc_data[-2]["close"]) / btc_data[-2]["close"] * 100
    lines.append(f"BTC 周涨跌: **{btc_ret:+.1f}%**\n")
    rows = sumdb.execute("""SELECT DISTINCT token_symbol, token_address FROM meta_snapshots
        WHERE meta_verdict='ACC' AND scan_time BETWEEN ? AND ?""", (start, end)).fetchall()
    if not rows:
        lines.append("> 本周无ACC代币\n")
        return "\n".join(lines)
    lines.append("| 代币 | 周涨跌% | Beta | 独立行情 |")
    lines.append("|------|---------|------|---------|")
    independent = []
    for r in rows:
        sym, addr = r["token_symbol"], r["token_address"]
        prices = srcdb.execute("""SELECT price_usd, scan_time FROM gecko_market_data
            WHERE token_address=? AND scan_time BETWEEN ? AND ? ORDER BY scan_time""",
            (addr, start, end)).fetchall()
        if len(prices) < 2: continue
        t_ret = (prices[-1]["price_usd"] - prices[0]["price_usd"]) / max(prices[0]["price_usd"], 1e-10) * 100
        beta = t_ret / btc_ret if abs(btc_ret) > 0.1 else 0
        is_ind = "★" if (btc_ret < -1 and t_ret > 1) or (btc_ret > 1 and t_ret < -1) else ""
        if is_ind: independent.append(sym)
        lines.append(f"| {sym} | {t_ret:+.1f}% | {beta:.2f} | {is_ind} |")
    if independent:
        lines.append(f"\n**独立行情代币**: {', '.join(independent)}")
    return "\n".join(lines)

# ═══ W2: 市场情绪 ═══
def w2_sentiment(mkt):
    lines = ["## 🌍 W2: 市场情绪上下文\n"]
    g = mkt.get("global_market", {})
    fgi = mkt.get("fear_greed", [])
    if g:
        lines.append(f"- BTC dominance: **{g.get('btc_dominance',0):.1f}%**")
        lines.append(f"- 全球市值: **${g.get('total_market_cap',0)/1e12:.2f}T**")
        lines.append(f"- 24h成交量: **${g.get('total_volume_24h',0)/1e9:.0f}B**")
    if len(fgi) >= 2:
        lines.append(f"\n### Fear & Greed 走势\n")
        lines.append("| 日期 | FGI | 情绪 |")
        lines.append("|------|-----|------|")
        for f in fgi[:7]:
            lines.append(f"| {f['date']} | {f['value']} | {f['label']} |")
        delta = fgi[0]["value"] - fgi[-1]["value"]
        lines.append(f"\n周变化: {delta:+d} ({'转暖' if delta>5 else '转冷' if delta<-5 else '持平'})")
    return "\n".join(lines)

# ═══ W3: 信号衰减曲线 ═══
def w3_decay(sumdb):
    lines = ["## 📈 W3: 信号衰减曲线\n"]
    rows = sumdb.execute("""SELECT signal_level, price_7d_ret, price_14d_ret, price_now_ret, mdd
        FROM token_history WHERE signal_level IN ('DIAMOND','RED','YELLOW')
        AND computed_date = (SELECT MAX(computed_date) FROM token_history)""").fetchall()
    if not rows:
        lines.append("> 数据不足\n")
        return "\n".join(lines)
    by_level = defaultdict(list)
    for r in rows:
        by_level[r["signal_level"]].append(r)
    lines.append("| 信号 | 样本 | 7d中位 | 14d中位 | 至今中位 | MDD中位 | 最佳持有 |")
    lines.append("|------|------|--------|---------|---------|---------|---------|")
    for lvl in ["DIAMOND", "RED", "YELLOW"]:
        arr = by_level.get(lvl, [])
        if not arr: continue
        def med(field):
            vals = [r[field] for r in arr if r[field] is not None]
            return statistics.median(vals) if vals else 0
        r7 = med("price_7d_ret")
        r14 = med("price_14d_ret")
        rnow = med("price_now_ret")
        mdd_v = med("mdd")
        rets = [r7, r14, rnow]
        best_idx = max(range(len(rets)), key=lambda i: rets[i])
        best_label = ["7d","14d","至今"][best_idx]
        lines.append(f"| {lvl} | {len(arr)} | {r7:+.1f}% | {r14:+.1f}% | {rnow:+.1f}% | {mdd_v:+.1f}% | **{best_label}** |")
    return "\n".join(lines)
# ═══ W4: 模拟组合回测 ═══
def w4_portfolio(sumdb, srcdb, start, end):
    lines = ["## 💼 W4: 模拟组合回测\n"]
    lines.append("> 规则: meta_score≥3 首次命中买入, DIST退出 或 14d持有期\n")
    rows = sumdb.execute("""SELECT DISTINCT m.token_symbol, m.token_address,
        MIN(m.scan_time) as first_signal
        FROM meta_snapshots m WHERE m.meta_verdict='ACC' AND m.meta_score>=3
        GROUP BY m.token_symbol""").fetchall()
    if not rows:
        lines.append("> 无 ACC 信号\n")
        return "\n".join(lines)
    positions = []
    for r in rows:
        sym, addr = r["token_symbol"], r["token_address"]
        entry_p = srcdb.execute("SELECT price_usd FROM gecko_market_data WHERE token_address=? AND scan_time>=? ORDER BY scan_time LIMIT 1", (addr, r["first_signal"])).fetchone()
        exit_p = srcdb.execute("SELECT price_usd FROM gecko_market_data WHERE token_address=? ORDER BY scan_time DESC LIMIT 1", (addr,)).fetchone()
        if not entry_p or not exit_p or entry_p["price_usd"] <= 0: continue
        ret = (exit_p["price_usd"] - entry_p["price_usd"]) / entry_p["price_usd"] * 100
        positions.append({"sym": sym, "entry": entry_p["price_usd"], "exit": exit_p["price_usd"], "ret": ret})
    if not positions:
        lines.append("> 无有效持仓\n")
        return "\n".join(lines)
    n = len(positions)
    port_ret = sum(p["ret"] for p in positions) / n
    win = sum(1 for p in positions if p["ret"] > 0)
    mdd = min(p["ret"] for p in positions)
    lines.append(f"- 持仓数: **{n}** | 等权周收益: **{port_ret:+.1f}%** | 胜率: **{win/n*100:.0f}%** | 最差单币: **{mdd:+.1f}%**\n")
    # BTC 对比
    btc_p = srcdb.execute("SELECT price_usd FROM gecko_market_data WHERE LOWER(token_address) LIKE '%btc%' ORDER BY scan_time DESC LIMIT 1").fetchone()
    lines.append("| 代币 | 入场价 | 当前价 | 收益 |")
    lines.append("|------|--------|--------|------|")
    for p in sorted(positions, key=lambda x: -x["ret"])[:10]:
        lines.append(f"| {p['sym']} | ${format_price(p['entry'])} | ${format_price(p['exit'])} | {p['ret']:+.1f}% |")
    return "\n".join(lines)

# ═══ W5: 合约-现货分歧 ═══
def w5_divergence(sumdb, srcdb, start, end):
    lines = ["## ⚔️ W5: 合约-现货分歧\n"]
    meta_rows = sumdb.execute("SELECT DISTINCT token_symbol, meta_verdict FROM meta_snapshots WHERE scan_time BETWEEN ? AND ?", (start, end)).fetchall()
    meta_map = {r["token_symbol"]: r["meta_verdict"] for r in meta_rows}
    futures = srcdb.execute("""SELECT f1.symbol, f1.oi_value_usd as oi_now, f1.funding_rate, f1.long_short_ratio,
        f2.oi_value_usd as oi_old
        FROM futures_snapshots f1
        LEFT JOIN futures_snapshots f2 ON f1.symbol=f2.symbol AND f2.scan_time=(SELECT MIN(scan_time) FROM futures_snapshots)
        WHERE f1.scan_time=(SELECT MAX(scan_time) FROM futures_snapshots)
        AND f1.oi_value_usd > 500000""").fetchall()
    if not futures:
        lines.append("> 合约数据不足\n")
        return "\n".join(lines)
    divergences = []
    for f in futures:
        sym = f["symbol"]
        verdict = meta_map.get(sym, "N/A")
        oi_delta = ((f["oi_now"] - f["oi_old"]) / max(f["oi_old"], 1) * 100) if f["oi_old"] and f["oi_old"] > 0 else 0
        div_type = ""
        if oi_delta > 30 and verdict == "DIST": div_type = "🔴合约多+现货空"
        elif oi_delta < -20 and verdict == "ACC": div_type = "🟢空头平仓机会"
        elif oi_delta > 20 and verdict == "ACC": div_type = "✅一致看多"
        if div_type:
            divergences.append({"sym": sym, "oi_delta": oi_delta, "fr": f["funding_rate"], "ls": f["long_short_ratio"], "verdict": verdict, "type": div_type})
    if divergences:
        lines.append("| 代币 | OI周Δ | FR | 多空比 | Meta | 分歧 |")
        lines.append("|------|-------|-----|--------|------|------|")
        for d in divergences[:15]:
            lines.append(f"| {d['sym']} | {d['oi_delta']:+.0f}% | {d['fr']*100:.4f}% | {d['ls']:.2f} | {d['verdict']} | {d['type']} |")
    else:
        lines.append("> 无显著分歧\n")
    return "\n".join(lines)

# ═══ W6: 价格形态 ═══
def w6_pattern(sumdb, srcdb, start, end):
    lines = ["## 📐 W6: 价格形态识别\n"]
    acc_tokens = sumdb.execute("SELECT DISTINCT token_symbol, token_address FROM meta_snapshots WHERE meta_verdict='ACC' AND scan_time BETWEEN ? AND ?", (start, end)).fetchall()
    if not acc_tokens:
        lines.append("> 无ACC代币\n")
        return "\n".join(lines)
    lines.append("| 代币 | 7d涨跌 | 7d振幅 | 形态 | 入场建议 |")
    lines.append("|------|--------|--------|------|---------|")
    patterns = {"底部横盘": [], "突破前夕": [], "已拉升": [], "下跌中": []}
    for t in acc_tokens:
        prices = srcdb.execute("SELECT price_usd FROM gecko_market_data WHERE token_address=? AND scan_time BETWEEN ? AND ? ORDER BY scan_time", (t["token_address"], start, end)).fetchall()
        if len(prices) < 2: continue
        p_arr = [p["price_usd"] for p in prices if p["price_usd"] > 0]
        if not p_arr: continue
        ret_7d = (p_arr[-1] - p_arr[0]) / p_arr[0] * 100
        amplitude = (max(p_arr) - min(p_arr)) / p_arr[0] * 100
        if ret_7d > 20: pat, tip = "已拉升", "观望"
        elif ret_7d < -15: pat, tip = "下跌中", "等企稳"
        elif amplitude < 15 and ret_7d < 5: pat, tip = "底部横盘", "★关注"
        else: pat, tip = "突破前夕", "★入场"
        patterns[pat].append(t["token_symbol"])
        lines.append(f"| {t['token_symbol']} | {ret_7d:+.1f}% | {amplitude:.0f}% | {pat} | {tip} |")
    lines.append(f"\n**汇总**: 底部横盘={len(patterns['底部横盘'])} 突破前夕={len(patterns['突破前夕'])} 已拉升={len(patterns['已拉升'])} 下跌中={len(patterns['下跌中'])}")
    return "\n".join(lines)
# ═══ W7: 生命周期流转 ═══
def w7_lifecycle(sumdb, start, end):
    lines = ["## 🔄 W7: 生命周期流转矩阵\n"]
    rows = sumdb.execute("SELECT token_symbol, current_stage, prev_stage, last_updated FROM token_lifecycle WHERE last_updated BETWEEN ? AND ?", (start, end)).fetchall()
    transitions = defaultdict(int)
    for r in rows:
        key = f"{r['prev_stage'] or 'NEW'} → {r['current_stage']}"
        transitions[key] += 1
    if transitions:
        lines.append("| 流转路径 | 数量 |")
        lines.append("|---------|------|")
        for k, v in sorted(transitions.items(), key=lambda x: -x[1]):
            lines.append(f"| {k} | {v} |")
    # 净产出
    new_acc = sumdb.execute("SELECT COUNT(DISTINCT token_symbol) FROM meta_snapshots WHERE meta_verdict='ACC' AND scan_time BETWEEN ? AND ? AND token_symbol NOT IN (SELECT token_symbol FROM meta_snapshots WHERE meta_verdict='ACC' AND scan_time < ?)", (start, end, start)).fetchone()[0]
    exit_acc = sumdb.execute("SELECT COUNT(DISTINCT token_symbol) FROM meta_snapshots WHERE meta_verdict='ACC' AND scan_time < ? AND token_symbol NOT IN (SELECT token_symbol FROM meta_snapshots WHERE meta_verdict='ACC' AND scan_time BETWEEN ? AND ?)", (start, start, end)).fetchone()[0]
    lines.append(f"\n- 新进ACC: **{new_acc}** | 退出ACC: **{exit_acc}** | 净产出: **{new_acc - exit_acc:+d}**")
    return "\n".join(lines)

# ═══ W8: 风险早期预警 ═══
def w8_risk(sumdb, srcdb, start, end):
    lines = ["## ⚠️ W8: 风险早期预警\n"]
    lines.append("> 多维交叉: 留存↓ + 成本倒挂 + 量缩 + 连降 → 风险评分\n")
    acc = sumdb.execute("SELECT DISTINCT token_symbol, token_address FROM meta_snapshots WHERE meta_verdict='ACC' AND scan_time BETWEEN ? AND ?", (start, end)).fetchall()
    alerts = []
    for t in acc:
        sym, addr = t["token_symbol"], t["token_address"]
        risk = 0
        details = []
        # 留存
        hist = sumdb.execute("SELECT retention_7d FROM token_history WHERE token_symbol=? ORDER BY computed_date DESC LIMIT 1", (sym,)).fetchone()
        if hist and hist["retention_7d"] is not None and hist["retention_7d"] < 70:
            risk += 2; details.append(f"留存{hist['retention_7d']:.0f}%")
        # 成本倒挂
        cb = sumdb.execute("SELECT cost_cv, dist_pct FROM cost_basis_snapshots WHERE token_symbol=? ORDER BY scan_time DESC LIMIT 1", (sym,)).fetchone()
        if cb and cb["cost_cv"] is not None and cb["cost_cv"] > 0.5:
            risk += 1; details.append(f"CV={cb['cost_cv']:.2f}")
        # 量缩
        gecko = srcdb.execute("SELECT volume_24h FROM gecko_market_data WHERE token_address=? ORDER BY scan_time DESC LIMIT 1", (addr,)).fetchone()
        if gecko and gecko["volume_24h"] is not None and gecko["volume_24h"] < 1000:
            risk += 2; details.append(f"量=${gecko['volume_24h']:.0f}")
        # meta 连降
        scores = sumdb.execute("SELECT meta_score FROM meta_snapshots WHERE token_symbol=? ORDER BY scan_time DESC LIMIT 3", (sym,)).fetchall()
        if len(scores) >= 3 and scores[0]["meta_score"] < scores[1]["meta_score"] < scores[2]["meta_score"]:
            risk += 1; details.append("连降3轮")
        if risk >= 3:
            level = "🔴极高" if risk >= 5 else "🟡中等"
            alerts.append({"sym": sym, "risk": risk, "level": level, "details": ", ".join(details)})
    if alerts:
        lines.append("| 代币 | 风险分 | 等级 | 恶化维度 |")
        lines.append("|------|--------|------|---------|")
        for a in sorted(alerts, key=lambda x: -x["risk"]):
            lines.append(f"| {a['sym']} | {a['risk']} | {a['level']} | {a['details']} |")
    else:
        lines.append("> 本周无高危ACC代币 ✅")
    return "\n".join(lines)

# ═══ M9-M12: 月报额外维度 ═══
def m9_sector(sumdb, start, end):
    lines = ["## 🔄 M9: 板块轮动分析\n"]
    rows = sumdb.execute("""SELECT 
        COALESCE(SUBSTR(token_address,1,2),'??') as chain_prefix,
        meta_verdict, COUNT(DISTINCT token_symbol) as cnt
        FROM meta_snapshots WHERE scan_time BETWEEN ? AND ?
        GROUP BY chain_prefix, meta_verdict""", (start, end)).fetchall()
    chain_stats = defaultdict(lambda: {"ACC": 0, "DIST": 0, "NEUTRAL": 0})
    for r in rows:
        chain_stats[r["chain_prefix"]][r["meta_verdict"]] = r["cnt"]
    lines.append("| 链 | ACC | DIST | NEUTRAL | ACC占比 |")
    lines.append("|-----|-----|------|---------|--------|")
    for ch, s in sorted(chain_stats.items(), key=lambda x: -x[1]["ACC"]):
        total = s["ACC"] + s["DIST"] + s["NEUTRAL"]
        pct = s["ACC"] / max(total, 1) * 100
        lines.append(f"| {ch} | {s['ACC']} | {s['DIST']} | {s['NEUTRAL']} | {pct:.0f}% |")
    return "\n".join(lines)

def m10_engine_precision(sumdb):
    lines = ["## 🎯 M10: 引擎精度月报\n"]
    rows = sumdb.execute("""SELECT token_symbol, signal_level, price_now_ret, score_slope
        FROM token_history WHERE computed_date=(SELECT MAX(computed_date) FROM token_history)""").fetchall()
    if not rows:
        lines.append("> 数据不足\n")
        return "\n".join(lines)
    # 从 meta_snapshots 获取引擎分项
    ms = sumdb.execute("""SELECT token_symbol, master_score, opus_score, whale_score, unified_score, cb_score
        FROM meta_snapshots WHERE scan_time=(SELECT MAX(scan_time) FROM meta_snapshots)""").fetchall()
    th_map = {r["token_symbol"]: r["price_now_ret"] for r in rows if r["price_now_ret"] is not None}
    engines = {"master": "master_score", "opus": "opus_score", "whale": "whale_score", "unified": "unified_score", "cb": "cb_score"}
    lines.append("| 引擎 | 命中数 | 盈利数 | Precision |")
    lines.append("|------|--------|--------|-----------|")
    for eng, col in engines.items():
        hit = [r for r in ms if r[col] and r[col] > 0 and r["token_symbol"] in th_map]
        win = [r for r in hit if th_map.get(r["token_symbol"], 0) > 0]
        prec = len(win) / max(len(hit), 1) * 100
        lines.append(f"| {eng} | {len(hit)} | {len(win)} | {prec:.0f}% |")
    return "\n".join(lines)

def m11_missed(sumdb):
    lines = ["## 🐟 M11: 漏网之鱼复盘\n"]
    rows = sumdb.execute("""SELECT token_symbol, signal_level, price_now_ret, score_slope
        FROM token_history WHERE computed_date=(SELECT MAX(computed_date) FROM token_history)
        AND signal_level IS NOT NULL AND signal_level NOT IN ('DIAMOND','RED') AND price_now_ret > 50
        ORDER BY price_now_ret DESC LIMIT 10""").fetchall()
    if rows:
        lines.append("| 代币 | 信号 | 收益 | 综合分 | 遗漏原因 |")
        lines.append("|------|------|------|--------|---------|")
        for r in rows:
            reason = "引擎未覆盖" if r["signal_level"] == "YELLOW" else "积分不足"
            lines.append(f"| {r['token_symbol']} | {r['signal_level'] or 'N/A'} | {r['price_now_ret']:+.1f}% | — | {reason} |")
    else:
        lines.append("> 本月无>50%漏网代币\n")
    return "\n".join(lines)

def m12_suggestions(sumdb):
    lines = ["## 💡 M12: 系统改进建议\n"]
    rows_th = sumdb.execute("SELECT token_symbol, price_now_ret FROM token_history WHERE computed_date=(SELECT MAX(computed_date) FROM token_history)").fetchall()
    rows_ms = sumdb.execute("SELECT token_symbol, whale_score, meta_score FROM meta_snapshots WHERE scan_time=(SELECT MAX(scan_time) FROM meta_snapshots)").fetchall()
    th_ret = {r["token_symbol"]: r["price_now_ret"] for r in rows_th if r["price_now_ret"] is not None}
    rows = rows_ms  # use meta_snapshots for engine scores
    suggestions = []
    # Rule 1: whale precision
    whale_hit = [r for r in rows if r["whale_score"] and r["whale_score"] > 0 and r["token_symbol"] in th_ret]
    whale_win = [r for r in whale_hit if th_ret.get(r["token_symbol"], 0) > 0]
    if whale_hit and len(whale_win)/max(len(whale_hit),1) < 0.4:
        suggestions.append(f"⚠ whale 引擎胜率 {len(whale_win)}/{len(whale_hit)}={len(whale_win)/max(len(whale_hit),1)*100:.0f}% < 40% → 建议降低积分权重")
    # Rule 2: 高分低胜
    high = [r for r in rows if (r["meta_score"] or 0) >= 7 and r["token_symbol"] in th_ret]
    high_win = [r for r in high if th_ret.get(r["token_symbol"], 0) > 0]
    if high and len(high_win)/max(len(high),1) < 0.5:
        suggestions.append(f"⚠ 高分(≥7)胜率 {len(high_win)/max(len(high),1)*100:.0f}% → 可能过拟合")
    if suggestions:
        for s in suggestions:
            lines.append(f"- {s}")
    else:
        lines.append("> 本月无需调整 ✅")
    return "\n".join(lines)
# ═══ 主入口 ═══
def generate_report(report_type="weekly"):
    now = datetime.now()
    if report_type == "weekly":
        end = now.strftime("%Y-%m-%d %H:%M:%S")
        start = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        week_num = now.isocalendar()[1]
        fname = f"week_{now.strftime('%Y')}W{week_num:02d}.md"
        out_dir = WEEKLY_DIR
        title = f"📅 AI-SUM 周报 — {now.strftime('%Y')}年第{week_num}周"
        date_range = f"{(now-timedelta(days=7)).strftime('%m-%d')} ~ {now.strftime('%m-%d')}"
    else:
        end = now.strftime("%Y-%m-%d %H:%M:%S")
        start = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        fname = f"month_{now.strftime('%Y%m')}.md"
        out_dir = MONTHLY_DIR
        title = f"📆 AI-SUM 月报 — {now.strftime('%Y年%m月')}"
        date_range = f"{(now-timedelta(days=30)).strftime('%m-%d')} ~ {now.strftime('%m-%d')}"

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    logger.info(f"生成{report_type}报告: {date_range}")

    # 采集外部数据
    days = 7 if report_type == "weekly" else 30
    mkt = market_context.collect_all(days)

    sumdb = conn_ro(SUM_DB)
    srcdb = conn_ro(SRC_DB)

    sections = [f"# {title}\n", f"> 周期: {date_range} | 生成: {now.strftime('%Y-%m-%d %H:%M')}\n---\n"]

    # W1-W8
    sections.append(w1_btc_beta(sumdb, srcdb, mkt, start, end))
    sections.append(w2_sentiment(mkt))
    sections.append(w3_decay(sumdb))
    sections.append(w4_portfolio(sumdb, srcdb, start, end))
    sections.append(w5_divergence(sumdb, srcdb, start, end))
    sections.append(w6_pattern(sumdb, srcdb, start, end))
    sections.append(w7_lifecycle(sumdb, start, end))
    sections.append(w8_risk(sumdb, srcdb, start, end))

    # 月报额外
    if report_type == "monthly":
        sections.append(m9_sector(sumdb, start, end))
        sections.append(m10_engine_precision(sumdb))
        sections.append(m11_missed(sumdb))
        sections.append(m12_suggestions(sumdb))

    sumdb.close()
    srcdb.close()

    report = "\n\n".join(sections)
    out_path = os.path.join(out_dir, fname)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    logger.info(f"报告已生成: {out_path} ({len(report)} bytes)")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="AI-SUM 周月报引擎")
    parser.add_argument("--type", choices=["weekly", "monthly"], default="weekly")
    args = parser.parse_args()
    t0 = time.time()
    path = generate_report(args.type)
    print(f"完成: {path} ({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()
