#!/usr/bin/env python3
"""
pump_detector v2 — 拉升前兆 9 维评分引擎
链上层 D1-D6 (BubbleMap+Gecko+Meta+History)
合约层 D7-D9 (Binance Futures)
输出: pump_alerts 表 + report/pump/pump_YYYYMMDD_HHMM.md
"""
from __future__ import annotations
import sqlite3, os, logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("pump_detector")

SELECT_DB = os.environ.get("SRC_DB_PATH", "/opt/select-coin/data/select.db")
SUM_DB    = os.environ.get("SUM_DB_PATH", "/opt/AI-SUM/select-sum.db")
REPORT_DIR = Path("/opt/AI-SUM/report/pump")

IMMINENT_THRESHOLD = 90
READY_THRESHOLD    = 65
WATCH_THRESHOLD    = 45

def _conn(db):
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row; return c

def ensure_pump_alerts_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS pump_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, scan_time TEXT NOT NULL,
        chain TEXT DEFAULT 'bsc', token_address TEXT NOT NULL, token_symbol TEXT,
        pump_score INTEGER, alert_level TEXT,
        d1_vol_shrink INTEGER, d2_lp_stable INTEGER, d3_acc_density INTEGER,
        d4_meta_persist INTEGER, d5_retention INTEGER, d6_score_quality INTEGER,
        d7_oi_change INTEGER, d8_funding INTEGER, d9_long_short INTEGER,
        acc_count INTEGER, acc_pct REAL, avg_acc_score REAL, meta_score REAL,
        consec_acc INTEGER, retention_7d REAL, vol_24h REAL, avg_vol_7d REAL,
        vol_ratio REAL, reserve_usd REAL, price_usd REAL, price_change_24h REAL,
        oi_value_usd REAL, oi_change_24h REAL, funding_rate REAL, long_short_ratio REAL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pump_time ON pump_alerts(scan_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pump_level ON pump_alerts(alert_level)")
    conn.commit()

# ── 数据加载 ──
def _load_bubblemap():
    c = _conn(SELECT_DB)
    rows = c.execute("""WITH latest AS (SELECT token_address, MAX(snapshot_time) as mx FROM bubblemap_holders GROUP BY token_address)
        SELECT bh.token_address, COUNT(*) as total_holders,
        SUM(CASE WHEN bh.is_accumulating=1 THEN 1 ELSE 0 END) as acc_count,
        AVG(CASE WHEN bh.is_accumulating=1 THEN bh.acc_score ELSE NULL END) as avg_acc_score
        FROM bubblemap_holders bh JOIN latest l ON bh.token_address=l.token_address AND bh.snapshot_time=l.mx
        GROUP BY bh.token_address""").fetchall()
    r = {x["token_address"]: dict(x) for x in rows}; c.close(); return r

def _load_gecko():
    c = _conn(SELECT_DB)
    latest = c.execute("""WITH latest AS (SELECT token_address, MAX(scan_time) as mx FROM gecko_market_data GROUP BY token_address)
        SELECT g.* FROM gecko_market_data g JOIN latest l ON g.token_address=l.token_address AND g.scan_time=l.mx""").fetchall()
    r = {x["token_address"]: dict(x) for x in latest}
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    for x in c.execute("SELECT token_address, AVG(volume_24h) as avg_vol_7d FROM gecko_market_data WHERE scan_time>=? AND volume_24h>0 GROUP BY token_address", (cutoff,)):
        if x["token_address"] in r: r[x["token_address"]]["avg_vol_7d"] = x["avg_vol_7d"]
    c.close(); return r

def _load_meta():
    c = _conn(SUM_DB)
    rows = c.execute("SELECT * FROM meta_snapshots WHERE scan_time=(SELECT MAX(scan_time) FROM meta_snapshots)").fetchall()
    r = {(x["token_address"] or "").lower(): dict(x) for x in rows}; c.close(); return r

def _load_history():
    c = _conn(SUM_DB)
    rows = c.execute("SELECT * FROM token_history WHERE computed_date=(SELECT MAX(computed_date) FROM token_history)").fetchall()
    r = {(x["token_address"] or "").lower(): dict(x) for x in rows}; c.close(); return r

def _load_names():
    c = _conn(SELECT_DB)
    rows = c.execute("SELECT LOWER(token_address) as addr, symbol, name FROM token_names").fetchall()
    r = {x["addr"]: x["symbol"] or x["name"] or "" for x in rows}; c.close(); return r

def _load_futures():
    """加载最新一轮合约数据"""
    c = _conn(SELECT_DB)
    try:
        rows = c.execute("""SELECT * FROM futures_snapshots
            WHERE scan_time=(SELECT MAX(scan_time) FROM futures_snapshots)""").fetchall()
        r = {(x["token_address"] or "").lower(): dict(x) for x in rows}
        logger.info(f"  Futures: {len(r)}")
        c.close()
        return r
    except Exception as e:
        logger.warning(f"  Futures 加载失败(表可能不存在): {e}")
        c.close()
        return {}

# ── 9 维评分 ──
def calc_pump_readiness(bm, gecko, meta, th, futures):
    dims = {}
    vol_24h = gecko.get("volume_24h",0) or 0
    avg_vol = gecko.get("avg_vol_7d",0) or 0
    vol_ratio = (vol_24h/avg_vol) if avg_vol>0 else (0 if vol_24h==0 else 1)

    # D1: 量缩 (0-20)
    dims["d1"] = 20 if vol_ratio<0.05 else 18 if vol_ratio<0.1 else 15 if vol_ratio<0.3 else 10 if vol_ratio<0.6 else 4 if vol_ratio<0.8 else 0
    # D1 修正: DEX量缩 + OI暴增 → 资金搬家非真量缩
    if futures:
        oi_chg = futures.get("oi_change_24h") or 0
        if vol_ratio < 0.3 and oi_chg > 0.20:
            old_d1 = dims["d1"]
            dims["d1"] = max(dims["d1"] - 8, 0)
            if old_d1 != dims["d1"]:
                logger.debug(f"  D1修正: {old_d1}→{dims['d1']} (DEX量缩但OI增{oi_chg:.1%})")

    # D2: LP (0-20)
    res = gecko.get("reserve_usd",0) or 0
    dims["d2"] = 20 if res>=500000 else 15 if res>=100000 else 10 if res>=50000 else 5 if res>=10000 else 0

    # D3: 吸筹密度 (0-20)
    acc = bm.get("acc_count",0) or 0; tot = bm.get("total_holders",300) or 300; pct = acc/tot
    dims["d3"] = 20 if pct>=0.40 else 18 if pct>=0.25 else 15 if pct>=0.15 else 10 if pct>=0.08 else 6 if pct>=0.04 else 0

    # D4: meta持续 (0-15)
    consec = (th.get("consec_acc",0) or 0) if th else 0
    ms = (meta.get("meta_score",0) or 0) if meta else 0
    dims["d4"] = 15 if consec>=25 else 12 if consec>=15 else 9 if consec>=10 else 6 if consec>=5 else 4 if ms>=3 else (4 if pct>=0.20 else 0)

    # D5: 留存 (0-10)
    ret = (th.get("retention_7d",0) or 0) if th else 0
    dims["d5"] = 10 if ret>=95 else 8 if ret>=90 else 6 if ret>=80 else 4 if ret>=70 else 0

    # D6: 均分 (0-10)
    avs = bm.get("avg_acc_score",0) or 0
    dims["d6"] = 10 if avs>=80 else 8 if avs>=75 else 6 if avs>=70 else 4 if avs>=60 else 0

    # ── 合约层 D7-D9 ──
    if futures:
        oi_val = futures.get("oi_value_usd") or 0
        oi_chg = futures.get("oi_change_24h") or 0
        price_chg = gecko.get("price_change_24h",0) or 0
        fr = futures.get("funding_rate") or 0
        fr_avg = futures.get("funding_rate_8h_avg") or 0
        ls = futures.get("long_short_ratio") or 1

        # D7: OI变化 (0-15) — OI增+价平=暗中建仓
        if oi_chg > 0.30 and abs(price_chg) < 5:     dims["d7"] = 15
        elif oi_chg > 0.20 and abs(price_chg) < 10:  dims["d7"] = 12
        elif oi_chg > 0.10:                           dims["d7"] = 8
        elif oi_chg > 0:                              dims["d7"] = 4
        elif oi_chg < -0.20:                          dims["d7"] = 0  # OI骤降=平仓出逃
        else:                                          dims["d7"] = 2
        # 小OI降权: OI<$1M → D7减半
        if oi_val < 1_000_000:
            dims["d7"] = dims["d7"] // 2
            logger.debug(f"  D7小OI降权: OI_val=${oi_val:,.0f}")

        # D8: Funding Rate (0-10) — 负值=空头拥挤
        fr_eff = fr_avg if fr_avg else fr
        if fr_eff < -0.0003:   dims["d8"] = 10  # 极度空头拥挤→轧空
        elif fr_eff < -0.0001: dims["d8"] = 7
        elif fr_eff < 0:       dims["d8"] = 4
        elif fr_eff > 0.001:   dims["d8"] = 0   # 多头过热→回调风险
        else:                   dims["d8"] = 2

        # D9: 多空比 (0-8) — 散户反指
        if ls < 0.6:   dims["d9"] = 8   # 散户重度做空→强反指
        elif ls < 0.8: dims["d9"] = 6
        elif ls < 1.0: dims["d9"] = 3
        elif ls > 2.5: dims["d9"] = 0   # 散户重度做多→反指看空
        elif ls > 2.0: dims["d9"] = 1
        else:           dims["d9"] = 2
    else:
        # 无合约数据→中间值(不惩罚不加分)
        dims["d7"] = 4
        dims["d8"] = 2
        dims["d9"] = 1

    return sum(dims.values()), dims

def classify(score, vol_ratio):
    if score>=IMMINENT_THRESHOLD: return "IMMINENT"
    if score>=READY_THRESHOLD: return "READY_VOL_SHRINK" if vol_ratio<0.3 else "READY"
    if score>=WATCH_THRESHOLD: return "WATCH"
    return "NEUTRAL"

def run(scan_time=None):
    scan_time = scan_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"[pump_detector v2] 开始扫描 scan_time={scan_time}")
    bm=_load_bubblemap(); logger.info(f"  BubbleMap: {len(bm)}")
    gecko=_load_gecko();  logger.info(f"  Gecko: {len(gecko)}")
    meta=_load_meta();    logger.info(f"  Meta: {len(meta)}")
    th=_load_history();   logger.info(f"  History: {len(th)}")
    names=_load_names()
    futures=_load_futures()

    results = []
    for addr in bm:
        b,g,m,h = bm[addr], gecko.get(addr,{}), meta.get(addr,{}), th.get(addr,{})
        ft = futures.get(addr, {})
        sym = names.get(addr, (m.get("token_symbol") or addr[:10]))
        score, dims = calc_pump_readiness(b, g, m, h, ft)
        v24 = g.get("volume_24h",0) or 0; av = g.get("avg_vol_7d",0) or 0
        vr = (v24/av) if av>0 else (0 if v24==0 else 1)
        lv = classify(score, vr)
        results.append({
            "scan_time":scan_time,"chain":"bsc","token_address":addr,
            "token_symbol":sym,"pump_score":score,"alert_level":lv,**dims,
            "acc_count":b.get("acc_count",0) or 0,
            "acc_pct":round((b.get("acc_count",0) or 0)/(b.get("total_holders",300) or 300)*100,1),
            "avg_acc_score":round(b.get("avg_acc_score",0) or 0,1),
            "meta_score":round((m.get("meta_score",0) or 0),2) if m else 0,
            "consec_acc":(h.get("consec_acc",0) or 0) if h else 0,
            "retention_7d":round((h.get("retention_7d",0) or 0),1) if h else 0,
            "vol_24h":round(v24,2),"avg_vol_7d":round(av,2),"vol_ratio":round(vr,4),
            "reserve_usd":round(g.get("reserve_usd",0) or 0,2),
            "price_usd":g.get("price_usd",0) or 0,
            "price_change_24h":round(g.get("price_change_24h",0) or 0,2),
            "oi_value_usd":ft.get("oi_value_usd") or 0,
            "oi_change_24h":ft.get("oi_change_24h") or 0,
            "funding_rate":ft.get("funding_rate") or 0,
            "long_short_ratio":ft.get("long_short_ratio") or 0,
        })
    results.sort(key=lambda x: x["pump_score"], reverse=True)
    _save_to_db(results)
    _generate_report(results, scan_time)
    levels = {}
    for r in results:
        levels[r["alert_level"]] = levels.get(r["alert_level"],0)+1
    for lv in ("IMMINENT","READY_VOL_SHRINK","READY","WATCH","NEUTRAL"):
        if lv in levels: logger.info(f"  {lv}: {levels[lv]}")
    has_ft = sum(1 for r in results if r.get("oi_value_usd",0)>0)
    logger.info(f"[pump_detector v2] 完成: {len(results)} 代币 (有合约数据: {has_ft})")
    return results

def _save_to_db(results):
    c = _conn(SUM_DB); ensure_pump_alerts_table(c)
    for r in results:
        if r["alert_level"]=="NEUTRAL": continue
        c.execute("""INSERT INTO pump_alerts (scan_time,chain,token_address,token_symbol,pump_score,alert_level,
            d1_vol_shrink,d2_lp_stable,d3_acc_density,d4_meta_persist,d5_retention,d6_score_quality,
            d7_oi_change,d8_funding,d9_long_short,
            acc_count,acc_pct,avg_acc_score,meta_score,consec_acc,retention_7d,
            vol_24h,avg_vol_7d,vol_ratio,reserve_usd,price_usd,price_change_24h,
            oi_value_usd,oi_change_24h,funding_rate,long_short_ratio)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r["scan_time"],r["chain"],r["token_address"],r["token_symbol"],
             r["pump_score"],r["alert_level"],
             r.get("d1",0),r.get("d2",0),r.get("d3",0),r.get("d4",0),r.get("d5",0),r.get("d6",0),
             r.get("d7",0),r.get("d8",0),r.get("d9",0),
             r["acc_count"],r["acc_pct"],r["avg_acc_score"],r["meta_score"],
             r["consec_acc"],r["retention_7d"],
             r["vol_24h"],r["avg_vol_7d"],r["vol_ratio"],
             r["reserve_usd"],r["price_usd"],r["price_change_24h"],
             r["oi_value_usd"],r["oi_change_24h"],r["funding_rate"],r["long_short_ratio"]))
    c.commit(); c.close()
    cnt = sum(1 for r in results if r["alert_level"]!="NEUTRAL")
    logger.info(f"  DB写入: {cnt} 条 (WATCH以上)")

def _generate_report(results, scan_time):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = scan_time.replace("-","").replace(":","").replace(" ","_")[:13]
    fp = REPORT_DIR / f"pump_{ts}.md"
    levels = {}
    for r in results: levels[r["alert_level"]]=levels.get(r["alert_level"],0)+1
    md = [f"# 🚀 拉升前兆扫描报告 v2",f"> 时间: {scan_time} | 代币: {len(results)} | 版本: 9维(链上+合约)",""]
    md.append("## 信号分布\n| 级别 | 数量 |\n|------|------|")
    for lv in ("IMMINENT","READY_VOL_SHRINK","READY","WATCH","NEUTRAL"):
        if lv in levels:
            ic = {"IMMINENT":"🔴","READY_VOL_SHRINK":"🟡","READY":"🟡","WATCH":"🟢","NEUTRAL":"—"}.get(lv,"")
            md.append(f"| {ic} {lv} | {levels[lv]} |")
    md.append("")
    # IMMINENT
    imm = [r for r in results if r["alert_level"]=="IMMINENT"]
    if imm:
        md.append(f"## 🔴 IMMINENT ({len(imm)})")
        md.append("| 代币 | pump | D1-D6 | D7 OI | D8 FR | D9 LS | 吸筹% | vol缩比 | OI($) | OI变化 | FR | L/S | LP |")
        md.append("|------|------|-------|-------|-------|-------|-------|---------|-------|--------|-----|-----|-----|")
        for r in imm:
            d16 = r.get("d1",0)+r.get("d2",0)+r.get("d3",0)+r.get("d4",0)+r.get("d5",0)+r.get("d6",0)
            oi_str = f"${r['oi_value_usd']:,.0f}" if r['oi_value_usd'] else "—"
            oi_chg = f"{r['oi_change_24h']:.1%}" if r['oi_change_24h'] else "—"
            fr_str = f"{r['funding_rate']:.4%}" if r['funding_rate'] else "—"
            ls_str = f"{r['long_short_ratio']:.2f}" if r['long_short_ratio'] else "—"
            md.append(f"| **{r['token_symbol']}** | **{r['pump_score']}** | {d16}/95 | {r.get('d7',0)} | {r.get('d8',0)} | {r.get('d9',0)} | {r['acc_pct']}% | {r['vol_ratio']:.3f} | {oi_str} | {oi_chg} | {fr_str} | {ls_str} | ${r['reserve_usd']:,.0f} |")
        md.append("")
    # READY
    rdy = [r for r in results if r["alert_level"].startswith("READY")]
    if rdy:
        md.append(f"## 🟡 READY ({len(rdy)})")
        md.append("| 代币 | pump | D1-6 | D7-9 | 吸筹% | vol缩比 | OI变化 | FR | L/S | LP |")
        md.append("|------|------|------|------|-------|---------|--------|-----|-----|-----|")
        for r in rdy[:25]:
            d16 = r.get("d1",0)+r.get("d2",0)+r.get("d3",0)+r.get("d4",0)+r.get("d5",0)+r.get("d6",0)
            d79 = r.get("d7",0)+r.get("d8",0)+r.get("d9",0)
            oi_chg = f"{r['oi_change_24h']:.1%}" if r.get('oi_change_24h') else "—"
            fr_str = f"{r['funding_rate']:.4%}" if r.get('funding_rate') else "—"
            ls_str = f"{r['long_short_ratio']:.2f}" if r.get('long_short_ratio') else "—"
            md.append(f"| {r['token_symbol']} | {r['pump_score']} | {d16} | {d79} | {r['acc_pct']}% | {r['vol_ratio']:.3f} | {oi_chg} | {fr_str} | {ls_str} | ${r['reserve_usd']:,.0f} |")
        md.append("")
    # 链上/合约分歧
    conflict = [r for r in results if r["alert_level"] in ("IMMINENT","READY_VOL_SHRINK","READY")
                and r.get("oi_change_24h") and r.get("oi_change_24h") < -0.10]
    if conflict:
        md.append("## ⚠ 链上/合约分歧（高评分但OI下降）")
        md.append("| 代币 | pump | OI变化 | 吸筹% | 说明 |")
        md.append("|------|------|--------|-------|------|")
        for r in conflict:
            md.append(f"| {r['token_symbol']} | {r['pump_score']} | {r['oi_change_24h']:.1%} | {r['acc_pct']}% | 链上吸筹但合约减仓 |")
        md.append("")

    with open(fp, "w", encoding="utf-8") as f: f.write("\n".join(md))
    logger.info(f"  报告: {fp}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    run()
