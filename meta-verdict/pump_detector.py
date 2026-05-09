#!/usr/bin/env python3
"""
pump_detector — 拉升前兆 6 维评分引擎
从 BubbleMap + Gecko + Meta + TokenHistory 四表计算 pump_readiness
输出: pump_alerts 表 + report/pump/pump_YYYYMMDD_HHMM.md
"""
from __future__ import annotations
import sqlite3, json, os, logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("pump_detector")

SELECT_DB = os.environ.get("SRC_DB_PATH", "/opt/select-coin/data/select.db")
SUM_DB    = os.environ.get("SUM_DB_PATH", "/opt/AI-SUM/select-sum.db")
REPORT_DIR = Path("/opt/AI-SUM/report/pump")

IMMINENT_THRESHOLD = 75
READY_THRESHOLD    = 50
WATCH_THRESHOLD    = 35

def _conn(db_path):
    c = sqlite3.connect(db_path); c.row_factory = sqlite3.Row; return c

def ensure_pump_alerts_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS pump_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, scan_time TEXT NOT NULL,
        chain TEXT DEFAULT 'bsc', token_address TEXT NOT NULL, token_symbol TEXT,
        pump_score INTEGER, alert_level TEXT,
        d1_vol_shrink INTEGER, d2_lp_stable INTEGER, d3_acc_density INTEGER,
        d4_meta_persist INTEGER, d5_retention INTEGER, d6_score_quality INTEGER,
        acc_count INTEGER, acc_pct REAL, avg_acc_score REAL, meta_score REAL,
        consec_acc INTEGER, retention_7d REAL, vol_24h REAL, avg_vol_7d REAL,
        vol_ratio REAL, reserve_usd REAL, price_usd REAL, price_change_24h REAL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pump_time ON pump_alerts(scan_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pump_level ON pump_alerts(alert_level)")
    conn.commit()

def _load_bubblemap():
    c = _conn(SELECT_DB)
    rows = c.execute("""WITH latest AS (SELECT token_address, MAX(snapshot_time) as mx FROM bubblemap_holders GROUP BY token_address)
        SELECT bh.token_address, bh.snapshot_time, COUNT(*) as total_holders,
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

def calc_pump_readiness(bm, gecko, meta, th):
    dims = {}
    vol_24h = gecko.get("volume_24h",0) or 0
    avg_vol = gecko.get("avg_vol_7d",0) or 0
    vol_ratio = (vol_24h/avg_vol) if avg_vol>0 else (0 if vol_24h==0 else 1)
    # D1: 量缩 (0-20)
    dims["d1"] = 20 if vol_ratio<0.05 else 18 if vol_ratio<0.1 else 15 if vol_ratio<0.3 else 10 if vol_ratio<0.6 else 4 if vol_ratio<0.8 else 0
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
    return sum(dims.values()), dims

def classify(score, vol_ratio):
    if score>=IMMINENT_THRESHOLD: return "IMMINENT"
    if score>=READY_THRESHOLD: return "READY_VOL_SHRINK" if vol_ratio<0.3 else "READY"
    if score>=WATCH_THRESHOLD: return "WATCH"
    return "NEUTRAL"

def run(scan_time=None):
    scan_time = scan_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info("[pump_detector] 开始扫描...")
    bm=_load_bubblemap(); logger.info(f"  BubbleMap: {len(bm)}")
    gecko=_load_gecko(); logger.info(f"  Gecko: {len(gecko)}")
    meta=_load_meta(); logger.info(f"  Meta: {len(meta)}")
    th=_load_history(); logger.info(f"  History: {len(th)}")
    names=_load_names()
    results = []
    for addr in bm:
        b,g,m,h = bm[addr], gecko.get(addr,{}), meta.get(addr,{}), th.get(addr,{})
        sym = names.get(addr, (m.get("token_symbol") or addr[:10]))
        score, dims = calc_pump_readiness(b, g, m, h)
        v24 = g.get("volume_24h",0) or 0; av = g.get("avg_vol_7d",0) or 0
        vr = (v24/av) if av>0 else (0 if v24==0 else 1)
        lv = classify(score, vr)
        results.append({"scan_time":scan_time,"chain":"bsc","token_address":addr,
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
            "price_change_24h":round(g.get("price_change_24h",0) or 0,2)})
    results.sort(key=lambda x: x["pump_score"], reverse=True)
    _save_to_db(results)
    _generate_report(results, scan_time)
    levels = {}
    for r in results:
        levels[r["alert_level"]] = levels.get(r["alert_level"],0)+1
    for lv in ("IMMINENT","READY_VOL_SHRINK","READY","WATCH","NEUTRAL"):
        if lv in levels: logger.info(f"  {lv}: {levels[lv]}")
    logger.info(f"[pump_detector] 完成: {len(results)} 代币")
    return results

def _save_to_db(results):
    c = _conn(SUM_DB); ensure_pump_alerts_table(c)
    for r in results:
        if r["alert_level"]=="NEUTRAL": continue
        c.execute("""INSERT INTO pump_alerts (scan_time,chain,token_address,token_symbol,pump_score,alert_level,
            d1_vol_shrink,d2_lp_stable,d3_acc_density,d4_meta_persist,d5_retention,d6_score_quality,
            acc_count,acc_pct,avg_acc_score,meta_score,consec_acc,retention_7d,
            vol_24h,avg_vol_7d,vol_ratio,reserve_usd,price_usd,price_change_24h)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r["scan_time"],r["chain"],r["token_address"],r["token_symbol"],
             r["pump_score"],r["alert_level"],
             r.get("d1",0),r.get("d2",0),r.get("d3",0),r.get("d4",0),r.get("d5",0),r.get("d6",0),
             r["acc_count"],r["acc_pct"],r["avg_acc_score"],r["meta_score"],
             r["consec_acc"],r["retention_7d"],
             r["vol_24h"],r["avg_vol_7d"],r["vol_ratio"],
             r["reserve_usd"],r["price_usd"],r["price_change_24h"]))
    c.commit(); c.close()
    logger.info(f"  DB: {sum(1 for r in results if r['alert_level']!='NEUTRAL')} 条")

def _generate_report(results, scan_time):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = scan_time.replace("-","").replace(":","").replace(" ","_")[:13]
    fp = REPORT_DIR / f"pump_{ts}.md"
    levels = {}
    for r in results: levels[r["alert_level"]]=levels.get(r["alert_level"],0)+1
    md = [f"# 🚀 拉升前兆扫描报告",f"> 时间: {scan_time} | 代币: {len(results)}",""]
    md.append("## 信号分布\n| 级别 | 数量 |\n|------|------|")
    for lv in ("IMMINENT","READY_VOL_SHRINK","READY","WATCH","NEUTRAL"):
        if lv in levels:
            ic = {"IMMINENT":"🔴","READY_VOL_SHRINK":"🟡","READY":"🟡","WATCH":"🟢","NEUTRAL":"—"}.get(lv,"")
            md.append(f"| {ic} {lv} | {levels[lv]} |")
    md.append("")
    imm = [r for r in results if r["alert_level"]=="IMMINENT"]
    if imm:
        md.append(f"## 🔴 IMMINENT ({len(imm)})\n| 代币 | pump | 吸筹 | 吸筹% | 均分 | meta | consec | ret% | vol缩比 | LP | 价格 | 24h% |\n|------|------|------|-------|------|------|--------|------|---------|----|----|------|")
        for r in imm:
            md.append(f"| **{r['token_symbol']}** | **{r['pump_score']}** | {r['acc_count']} | {r['acc_pct']}% | {r['avg_acc_score']} | {r['meta_score']} | {r['consec_acc']} | {r['retention_7d']}% | {r['vol_ratio']:.3f} | ${r['reserve_usd']:,.0f} | ${r['price_usd']:.6f} | {r['price_change_24h']}% |")
        md.append("")
    rdy = [r for r in results if r["alert_level"].startswith("READY")]
    if rdy:
        md.append(f"## 🟡 READY ({len(rdy)})\n| 代币 | pump | 吸筹% | vol缩比 | LP | meta |\n|------|------|-------|---------|----|----|")
        for r in rdy[:20]:
            md.append(f"| {r['token_symbol']} | {r['pump_score']} | {r['acc_pct']}% | {r['vol_ratio']:.3f} | ${r['reserve_usd']:,.0f} | {r['meta_score']} |")
        md.append("")
    with open(fp, "w", encoding="utf-8") as f: f.write("\n".join(md))
    logger.info(f"  报告: {fp}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    run()
