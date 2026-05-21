#!/usr/bin/env python3
"""
pump_detector.py v3 (2026-05-21, 10维100分权重体系)
从 BubbleMap + Gecko + Meta + TokenHistory + Futures 五表计算 10 维 pump_readiness 评分，分级输出告警。

v3 变更:
  - 评分体系从 9 维 128 分 → 10 维 100 分
  - 新增 D10 Pool 稳定性 (满分 8, 基于 gecko_market_data 7d LP 波动率)
  - 新增 S11(SILENT_ACC) 静默建仓信号 (LP极稳+OI缓增+FR负+均分升)
  - pump_alerts 表自动升级 d10_score / silent_acc 列
  - 告警阈值: IMMINENT ≥70 | READY ≥50 | WATCH ≥35

维度权重 (满分 100):
  D1 量缩(15) | D2 LP规模(12) | D3 浓度(15) | D4 Meta持续(10) | D5 留存(7) |
  D6 双轨(7) | D7 OI变化(12) | D8 FR(8) | D9 LS(6) | D10 Pool稳定(8)
"""
import sqlite3
import statistics
from datetime import datetime, timedelta
from pathlib import Path

SRC_DB = "/opt/select-coin/data/select.db"
SUM_DB = "/opt/AI-SUM/select-sum.db"
REPORT_DIR = "/opt/AI-SUM/report/pump"


def connect(path, readonly=False):
    uri = f"file:{path}?mode=ro" if readonly else path
    c = sqlite3.connect(uri, uri=readonly)
    c.row_factory = sqlite3.Row
    return c


def get_acc_concentration_score(concentration):
    """
    D3 评分：真实用户吸筹浓度分 (满分 15 分)
    阈值区间与 v2 对齐，分值等比压缩至 15 分满分。
    """
    if concentration is None:
        return 0
    if concentration >= 40.0:
        return 15
    elif concentration >= 25.0:
        return 12
    elif concentration >= 15.0:
        return 9
    elif concentration >= 8.0:
        return 6
    elif concentration >= 3.0:
        return 3
    return 0


def get_macro_micro_score(avg_acc_score, avg_macro_score):
    """
    D6 评分：精英均分+大盘门控双轨制 (满分 7 分)
    """
    if avg_acc_score is None or avg_acc_score <= 0:
        return 0

    # 1. 主轴精英打分
    if avg_acc_score >= 80:
        score = 6
    elif avg_acc_score >= 70:
        score = 4
    elif avg_acc_score >= 60:
        score = 3
    elif avg_acc_score >= 40:
        score = 1
    else:
        score = 0

    # 2. 大盘分门控修正
    if avg_macro_score is not None:
        if avg_macro_score < 44:
            score -= 2
        elif avg_macro_score >= 53:
            score += 1

    return max(0, min(7, score))


def get_pool_stability_score(src, token_address):
    """
    D10 评分：Pool 稳定性 (满分 8 分)
    基于 gecko_market_data 近 7 天 reserve_usd 时序数据。

    子项:
      - LP 7d 波动率 (max 4): 极稳(<3%)→4, 稳(<8%)→3, 正常(<15%)→2, 波动(<25%)→1
      - LP 规模 (max 2): ≥$500K→2, ≥$100K→1
      - 交易活跃度 (max 2): ≥500txns→2, ≥100→1

    返回: (d10_score, lp_volatility_pct, reserve_latest, txns_24h)
    """
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    rows = src.execute("""
        SELECT reserve_usd, buys_24h, sells_24h
        FROM gecko_market_data
        WHERE token_address = ? AND scan_time >= ?
        ORDER BY scan_time DESC
    """, [token_address, week_ago]).fetchall()

    if not rows:
        return 0, None, 0, 0

    reserves = [r["reserve_usd"] or 0 for r in rows if (r["reserve_usd"] or 0) > 0]
    latest = rows[0]
    reserve_latest = latest["reserve_usd"] or 0
    txns = (latest["buys_24h"] or 0) + (latest["sells_24h"] or 0)

    # 子项1: LP 波动率 (max 4)
    lp_vol_score = 0
    lp_vol_pct = None
    if len(reserves) >= 2:
        r_max, r_min = max(reserves), min(reserves)
        lp_vol_pct = round((r_max - r_min) / r_max * 100, 1) if r_max > 0 else 100
        if lp_vol_pct < 3:
            lp_vol_score = 4
        elif lp_vol_pct < 8:
            lp_vol_score = 3
        elif lp_vol_pct < 15:
            lp_vol_score = 2
        elif lp_vol_pct < 25:
            lp_vol_score = 1

    # 子项2: LP 规模 (max 2)
    lp_size_score = 0
    if reserve_latest >= 500_000:
        lp_size_score = 2
    elif reserve_latest >= 100_000:
        lp_size_score = 1

    # 子项3: 交易活跃度 (max 2)
    activity_score = 0
    if txns >= 500:
        activity_score = 2
    elif txns >= 100:
        activity_score = 1

    d10_total = lp_vol_score + lp_size_score + activity_score
    return d10_total, lp_vol_pct, reserve_latest, txns


def calc_pump_readiness(src, sumdb, token_address, token_symbol, scan_time):
    """计算单币 10 维拉升指数 (满分 100)"""
    addr = token_address.lower() if token_address else ""

    # D1 & D2: gecko 交易活跃度与 LP
    latest_gecko = src.execute("""
        SELECT volume_24h, reserve_usd, price_change_24h
        FROM gecko_market_data WHERE token_address = ?
        ORDER BY scan_time DESC LIMIT 1
    """, [token_address]).fetchone()

    d1_score = 0
    d2_score = 0
    vol_ratio = 1.0
    reserve = 0
    vol_24h = 0
    price_chg = 0

    if latest_gecko:
        vol_24h = latest_gecko["volume_24h"] or 0
        reserve = latest_gecko["reserve_usd"] or 0
        price_chg = latest_gecko["price_change_24h"] or 0

        # D2 LP规模 (12分)
        if reserve >= 500000:
            d2_score = 12
        elif reserve >= 100000:
            d2_score = 9
        elif reserve >= 50000:
            d2_score = 6
        elif reserve >= 10000:
            d2_score = 3

        # 7d 均量
        avg7 = src.execute("""
            SELECT AVG(volume_24h) FROM (
                SELECT volume_24h FROM gecko_market_data
                WHERE token_address = ? AND volume_24h > 0
                ORDER BY scan_time DESC LIMIT 14
            )
        """, [token_address]).fetchone()[0] or 0

        if avg7 > 0:
            vol_ratio = vol_24h / avg7
            # D1 量缩 (15分)
            if vol_24h < 1000:
                d1_score = 0
            elif vol_ratio <= 0.3:
                d1_score = 15
            elif vol_ratio <= 0.6:
                d1_score = 11
            elif vol_ratio <= 1.0:
                d1_score = 7
            elif vol_ratio <= 1.5:
                d1_score = 4

    # D3 & D6: 真实吸筹浓度与大盘/精英双轨制得分
    latest_bm = src.execute("""
        SELECT MAX(snapshot_time) FROM bubblemap_holders WHERE token_address = ?
    """, [token_address]).fetchone()[0]

    concentration = 0
    d3_score = 0
    d6_score = 0
    real_users = 0
    acc_count = 0
    avg_acc_score = 0
    avg_macro_score = 0
    control_level = "None"

    if latest_bm:
        totals = src.execute("""
            SELECT
                SUM(CASE WHEN is_cex=0 AND is_dex=0 AND is_contract=0 THEN 1 ELSE 0 END) as real_user_count,
                SUM(CASE WHEN is_accumulating=1 THEN 1 ELSE 0 END) as acc_count,
                AVG(CASE WHEN is_accumulating=1 THEN acc_score ELSE NULL END) as avg_acc_score,
                AVG(CASE WHEN is_cex=0 AND is_dex=0 AND is_contract=0 THEN acc_score ELSE NULL END) as avg_macro_score,
                MAX(control_level) as max_control
            FROM bubblemap_holders
            WHERE token_address = ? AND snapshot_time = ?
        """, [token_address, latest_bm]).fetchone()

        if totals and totals["real_user_count"]:
            real_users = totals["real_user_count"]
            acc_count = totals["acc_count"] or 0
            concentration = (acc_count / real_users) * 100
            d3_score = get_acc_concentration_score(concentration)

            avg_acc_score = totals["avg_acc_score"] or 0
            avg_macro_score = totals["avg_macro_score"] or 0
            d6_score = get_macro_micro_score(avg_acc_score, avg_macro_score)
            
            control_level = totals["max_control"] or "None"
            
            # A/B 级庄控强行赋 D3 满分 15 分
            if control_level in ("A", "B"):
                d3_score = 15

    # ─── 计算大户深套率 underwater_ratio ───
    underwater_ratio = 0.0
    latest_gmgn = src.execute("""
        SELECT MAX(snapshot_time) FROM gmgn_holders WHERE token_address = ?
    """, [token_address]).fetchone()[0]
    
    if latest_gmgn:
        gmgn_totals = src.execute("""
            SELECT 
                COUNT(*) as total_holders,
                SUM(CASE WHEN unrealized_pnl < 0 THEN 1 ELSE 0 END) as loss_holders
            FROM gmgn_holders
            WHERE token_address = ? AND snapshot_time = ? AND hold_amount > 0
        """, [token_address, latest_gmgn]).fetchone()
        
        if gmgn_totals and gmgn_totals["total_holders"] > 0:
            underwater_ratio = (gmgn_totals["loss_holders"] or 0) / gmgn_totals["total_holders"]

    # 🤫 隐秘爆破判定
    stealth_pump = False
    if control_level in ("A", "B") and underwater_ratio > 0.90:
        stealth_pump = True

    # D4 & D5: Meta持续与留存率
    latest_meta = sumdb.execute("""
        SELECT meta_score, meta_verdict FROM meta_snapshots
        WHERE token_symbol = ? AND scan_time <= ?
        ORDER BY scan_time DESC LIMIT 1
    """, [token_symbol, scan_time]).fetchone()

    d4_score = 0
    d5_score = 0
    consec_acc = 0
    retention_7d = 0

    if latest_meta and latest_meta["meta_verdict"] == "ACC":
        consec_rows = sumdb.execute("""
            SELECT consec_acc FROM token_history
            WHERE token_symbol = ? AND computed_date <= ?
            ORDER BY computed_date DESC LIMIT 1
        """, [token_symbol, scan_time[:10]]).fetchone()
        consec_acc = consec_rows["consec_acc"] if consec_rows else 0

        # D4 Meta持续 (10分)
        if consec_acc >= 25:
            d4_score = 10
        elif consec_acc >= 15:
            d4_score = 8
        elif consec_acc >= 8:
            d4_score = 5
        elif consec_acc >= 3:
            d4_score = 3

        # D5 留存 (7分)
        th_row = sumdb.execute("""
            SELECT retention_7d FROM token_history
            WHERE token_symbol = ? AND computed_date <= ?
            ORDER BY computed_date DESC LIMIT 1
        """, [token_symbol, scan_time[:10]]).fetchone()
        if th_row and th_row["retention_7d"]:
            retention_7d = th_row["retention_7d"]
            if retention_7d >= 95:
                d5_score = 7
            elif retention_7d >= 85:
                d5_score = 5
            elif retention_7d >= 75:
                d5_score = 4
            elif retention_7d >= 60:
                d5_score = 3

    # D7, D8, D9: futures_snapshots
    latest_ft = src.execute("""
        SELECT oi_value_usd, oi_change_24h, funding_rate, long_short_ratio
        FROM futures_snapshots WHERE token_address = ?
        ORDER BY scan_time DESC LIMIT 1
    """, [token_address]).fetchone()

    d7_score = 0
    d8_score = 0
    d9_score = 0
    oi_usd = 0
    oi_chg = 0
    fr = 0
    ls = 0

    if latest_ft:
        oi_usd = latest_ft["oi_value_usd"] or 0
        oi_chg = latest_ft["oi_change_24h"] or 0
        fr = latest_ft["funding_rate"] or 0
        ls = latest_ft["long_short_ratio"] or 0

        # 过滤搬家假量缩：DEX量缩+OI暴增时调降D1量缩分
        if oi_chg > 0.15 and vol_ratio <= 0.3 and d1_score == 15:
            d1_score = 7

        # D7 OI变化 (12分)
        if oi_usd >= 1000000:
            if oi_chg >= 0.10:
                d7_score = 12
            elif oi_chg >= 0.03:
                d7_score = 8
            elif oi_chg >= -0.05:
                d7_score = 4
        else:
            if oi_chg >= 0.10:
                d7_score = 6
            elif oi_chg >= 0.03:
                d7_score = 4
            elif oi_chg >= -0.05:
                d7_score = 2

        # D8 FR (8分)
        if fr <= -0.0003:
            d8_score = 8
        elif fr <= -0.0001:
            d8_score = 6
        elif fr <= 0:
            d8_score = 4
        elif fr <= 0.0005:
            d8_score = 1

        # D9 L/S (6分)
        if ls <= 0.6:
            d9_score = 6
        elif ls <= 0.9:
            d9_score = 4
        elif ls <= 1.2:
            d9_score = 3
        elif ls <= 1.5:
            d9_score = 1

    # D10 Pool稳定性 (8分)
    d10_score, lp_vol_pct, _, d10_txns = get_pool_stability_score(src, token_address)

    total_score = (d1_score + d2_score + d3_score + d4_score + d5_score
                   + d6_score + d7_score + d8_score + d9_score + d10_score)

    # 分级 (满分100)
    level = "WATCH"
    if total_score >= 70:
        level = "IMMINENT"
    elif total_score >= 50:
        if vol_ratio <= 0.3:
            level = "READY_VOL_SHRINK"
        else:
            level = "READY"
    elif total_score >= 35:
        level = "WATCH"
    else:
        level = "NONE"

    # S11 静默建仓信号 (独立于 C10)
    silent_acc = False
    if (lp_vol_pct is not None and lp_vol_pct < 5
            and d7_score >= 2 and d8_score >= 4
            and avg_acc_score > avg_macro_score and avg_acc_score > 0):
        silent_acc = True

    return {
        "symbol": token_symbol, "addr": token_address,
        "score": total_score, "level": level,
        "d1": d1_score, "d2": d2_score, "d3": d3_score, "d4": d4_score,
        "d5": d5_score, "d6": d6_score, "d7": d7_score, "d8": d8_score,
        "d9": d9_score, "d10": d10_score,
        "concentration": concentration, "vol_ratio": vol_ratio,
        "oi_usd": oi_usd, "oi_chg": oi_chg, "fr": fr, "ls": ls, "reserve": reserve,
        "avg_acc_score": avg_acc_score, "avg_macro_score": avg_macro_score,
        "lp_vol_pct": lp_vol_pct, "silent_acc": silent_acc,
        "underwater_ratio": round(underwater_ratio, 4),
        "control_level": control_level,
        "stealth_pump": stealth_pump,
    }


def ensure_pump_alerts_table(sumdb):
    """创建 pump_alerts 表 + v3 自动升级"""
    sumdb.execute("""
        CREATE TABLE IF NOT EXISTS pump_alerts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time       TEXT NOT NULL,
            token_symbol    TEXT NOT NULL,
            token_address   TEXT NOT NULL,
            pump_score      REAL NOT NULL,
            alert_level     TEXT NOT NULL,
            d1_score        REAL DEFAULT 0,
            d2_score        REAL DEFAULT 0,
            d3_score        REAL DEFAULT 0,
            d4_score        REAL DEFAULT 0,
            d5_score        REAL DEFAULT 0,
            d6_score        REAL DEFAULT 0,
            d7_score        REAL DEFAULT 0,
            d8_score        REAL DEFAULT 0,
            d9_score        REAL DEFAULT 0,
            concentration   REAL DEFAULT 0,
            vol_ratio       REAL DEFAULT 0,
            oi_usd          REAL DEFAULT 0,
            oi_chg          REAL DEFAULT 0,
            fr              REAL DEFAULT 0,
            ls              REAL DEFAULT 0,
            reserve_usd     REAL DEFAULT 0,
            UNIQUE(scan_time, token_address)
        )
    """)
    # v3 升级: 新增 d10_score / silent_acc 列
    existing = {row[1] for row in sumdb.execute("PRAGMA table_info(pump_alerts)").fetchall()}
    v3_cols = {"d10_score": "REAL DEFAULT 0", "silent_acc": "INTEGER DEFAULT 0"}
    for col_name, col_def in v3_cols.items():
        if col_name not in existing:
            sumdb.execute(f"ALTER TABLE pump_alerts ADD COLUMN {col_name} {col_def}")
            
    # v4 升级: 新增 underwater_ratio / control_level / stealth_pump 列
    v4_cols = {
        "underwater_ratio": "REAL DEFAULT 0",
        "control_level": "TEXT DEFAULT 'None'",
        "stealth_pump": "INTEGER DEFAULT 0"
    }
    for col_name, col_def in v4_cols.items():
        if col_name not in existing:
            sumdb.execute(f"ALTER TABLE pump_alerts ADD COLUMN {col_name} {col_def}")
            
    sumdb.commit()


def save_pump_alerts(sumdb, scan_time, results):
    """保存记录到 DB"""
    saved = 0
    for r in results:
        if r["level"] == "NONE":
            continue
        sumdb.execute("""
            INSERT OR REPLACE INTO pump_alerts
            (scan_time, token_symbol, token_address, pump_score, alert_level,
             d1_score, d2_score, d3_score, d4_score, d5_score, d6_score,
             d7_score, d8_score, d9_score, d10_score,
             concentration, vol_ratio, oi_usd, oi_chg, fr, ls, reserve_usd, silent_acc,
             underwater_ratio, control_level, stealth_pump)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            scan_time, r["symbol"], r["addr"], r["score"], r["level"],
            r["d1"], r["d2"], r["d3"], r["d4"], r["d5"], r["d6"],
            r["d7"], r["d8"], r["d9"], r["d10"],
            r["concentration"], r["vol_ratio"], r["oi_usd"], r["oi_chg"],
            r["fr"], r["ls"], r["reserve"],
            1 if r.get("silent_acc") else 0,
            r.get("underwater_ratio", 0.0),
            r.get("control_level", "None"),
            1 if r.get("stealth_pump") else 0,
        ))
        saved += 1
    sumdb.commit()
    return saved


def generate_report(scan_time, results):
    """生成 Markdown 看板报告"""
    lines = [f"# 🚀 AI-SUM 拉升前兆引擎报告 (v3) — {scan_time}\n"]
    lines.append("> 基于 BubbleMap + Gecko + Futures 10 维评分体系 (满分 100)")
    lines.append("> 每 6 小时生成一次 (与 history_report.py 对齐)")
    lines.append("")

    # ─── 🤫 STEALTH_PUMP_READY 极秘爆破看板 ───
    stealth_tokens = [r for r in results if r.get("stealth_pump")]
    lines.append("## 🤫 STEALTH_PUMP_READY 极秘爆破看板")
    if stealth_tokens:
        lines.append("> ⚠️ **警告**：强控盘庄家深度套牢大户（深套率 > 90%），空头爆破一触即发！")
        lines.append("")
        lines.append("| 代币 | pump评分 | 庄控级别 | 大户深套率 | 浓度分(D3) | vol缩比(D1) | LP 7d波动% |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for r in stealth_tokens:
            lp_vol = f"{r.get('lp_vol_pct')}%" if r.get('lp_vol_pct') is not None else "—"
            lines.append(
                f"| **{r['symbol']}** | **{r['score']}** | `🤫 {r.get('control_level', 'None')}` "
                f"| {r.get('underwater_ratio', 0.0):.1%} | {r['d3']} | {r['vol_ratio']:.2f}x | {lp_vol} |"
            )
    else:
        lines.append("> [!TIP]")
        lines.append("> **🤫 STEALTH_PUMP_READY 极秘爆破机制**")
        lines.append("> 当检测到 A/B 级强庄控且大户深套率 `underwater_ratio > 90%` 时，将在此置顶触发爆破预警。目前暂无代币触发，主力筹码尚在整理中。")
    lines.append("")

    lines.append("## 📖 报表说明 (Tips)")
    lines.append("- **pump**: 拉升就绪评分 (满分 100). `IMMINENT` ≥70 (🔴) | `READY` ≥50 (🟡) | `WATCH` ≥35 (🟢)")
    lines.append("- **D1-D6**: 链上评分 (合计 66 分). D1量缩(15) | D2 LP规模(12) | D3吸筹浓度(15) | D4 Meta持续(10) | D5留存(7) | D6精英/大盘双轨(7)")
    lines.append("- **D7-D9**: 合约评分 (合计 26 分). D7 OI变化(12) | D8 资金费率(8) | D9 多空比(6)")
    lines.append("- **D10**: Pool 稳定性 (8 分). LP 7d波动率(4) + LP规模(2) + 交易活跃度(2)")
    lines.append("- **🤫**: S11 静默建仓信号 — LP极稳 + OI缓增 + FR负 + 链上均分持续上升")
    lines.append("- **吸筹%**: 真实吸筹浓度 (排除交易所/合约/CEX后的独立ACC地址占比)")
    lines.append("- **vol缩比**: 24h量/7d均量 (≤0.3x 为强力锁仓量缩)")
    lines.append("- **L/S**: 合约主动多空人数比 (<0.6 散户开空做反指)")
    lines.append("")

    def _fmtk(v):
        if v is None:
            return "—"
        if v >= 1e6:
            return f"${v/1e6:.1f}M"
        if v >= 1e3:
            return f"${v/1e3:.0f}K"
        return f"${v:.0f}"

    def _render_table(level_results, title, emoji):
        if not level_results:
            return ""
        table = [
            f"### {emoji} {title} ({len(level_results)} 个)",
            "",
            "| 代币 | pump | D1-D6 | D7 | D8 | D9 | D10 | 🤫 | 吸筹% | vol缩比 | OI($) | OI变化 | FR | L/S | LP |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
        ]
        for r in level_results:
            d16 = f"{r['d1']}+{r['d2']}+{r['d3']:.0f}+{r['d4']}+{r['d5']}+{r['d6']}"
            oi_usd_str = _fmtk(r['oi_usd'])
            reserve_str = _fmtk(r['reserve'])
            
            flags = []
            if r.get("stealth_pump"):
                flags.append("🤫 STEALTH_PUMP_READY")
            elif r.get("silent_acc"):
                flags.append("🤫")
            silent_flag = " / ".join(flags) if flags else ""
            
            table.append(
                f"| **{r['symbol']}** | **{r['score']}** | {d16} | {r['d7']} | {r['d8']} | {r['d9']} "
                f"| {r['d10']} | {silent_flag} "
                f"| {r['concentration']:.1f}% | {r['vol_ratio']:.2f}x | {oi_usd_str} | {r['oi_chg']:+.1%} "
                f"| {r['fr']:.4%} | {r['ls']:.2f} | {reserve_str} |"
            )
        table.append("")
        return "\n".join(table)

    # 🔴 IMMINENT
    imminent = [r for r in results if r["level"] == "IMMINENT"]
    lines.append(_render_table(imminent, "IMMINENT 即将拉升", "🔴"))

    # 🟡 READY
    ready = [r for r in results if r["level"] in ("READY", "READY_VOL_SHRINK")]
    lines.append(_render_table(ready, "READY 量缩/就绪", "🟡"))

    # 🟢 WATCH
    watch = [r for r in results if r["level"] == "WATCH"]
    lines.append(_render_table(watch, "WATCH 观察阶段", "🟢"))

    # 链上/合约分歧检测
    divergence = [r for r in results if r["score"] >= 50 and r["oi_chg"] < -0.10]
    if divergence:
        lines.append("## ⚠️ 链上/合约分歧检测 (高评分但合约退潮)")
        lines.append("| 代币 | pump评分 | 链上吸筹浓度 | vol缩比 | OI变化24h | LP | 含义 |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for r in divergence:
            lines.append(f"| {r['symbol']} | {r['score']} | {r['concentration']:.1f}% | {r['vol_ratio']:.2f}x | {r['oi_chg']:+.1%} | {_fmtk(r['reserve'])} | 链上高度建仓，但衍生品市场减仓离场 |")
        lines.append("")

    # 🤫 静默建仓信号汇总
    silent_tokens = [r for r in results if r.get("silent_acc")]
    if silent_tokens:
        lines.append("## 🤫 S11 静默建仓信号")
        lines.append("> LP 极稳 + OI 缓增 + 资金费率持续为负 + 链上吸筹均分上升 = 主力悄悄布局")
        lines.append("")
        lines.append("| 代币 | pump | D10 | LP波动% | 吸筹均分 | 大盘分 | OI | FR |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for r in silent_tokens:
            lines.append(
                f"| **{r['symbol']}** | **{r['score']}** | {r['d10']} "
                f"| {r.get('lp_vol_pct', '—')}% "
                f"| {r['avg_acc_score']:.1f} | {r['avg_macro_score']:.1f} "
                f"| {_fmtk(r['oi_usd'])} | {r['fr']:.4%} |"
            )
        lines.append("")

    return "\n".join(lines)


def run(scan_time=None):
    if not scan_time:
        scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"AI-SUM 拉升前兆扫描开始 (v3, 10维100分) | {scan_time}")
    src = connect(SRC_DB, readonly=True)
    sumdb = connect(SUM_DB)

    ensure_pump_alerts_table(sumdb)

    # 扫描 watchlist 中的代币
    tokens = sumdb.execute("""
        SELECT DISTINCT token_address, token_symbol FROM watchlist
        WHERE token_address IS NOT NULL AND token_address != ''
    """).fetchall()

    results = []
    for t in tokens:
        try:
            res = calc_pump_readiness(src, sumdb, t["token_address"], t["token_symbol"], scan_time)
            results.append(res)
        except Exception as e:
            print(f"  代币 {t['token_symbol']} 扫描失败: {e}")

    src.close()

    # 按分数排序
    results.sort(key=lambda x: x["score"], reverse=True)

    # 存盘
    saved = save_pump_alerts(sumdb, scan_time, results)
    silent_cnt = sum(1 for r in results if r.get("silent_acc"))
    print(f"  [DB] 保存 {saved} 个告警代币, 其中 S11 静默建仓 {silent_cnt} 个")

    # 生成 Markdown 报告
    md = generate_report(scan_time, results)
    out_dir = Path(REPORT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"pump_{scan_time[:10].replace('-','')}_{scan_time[11:16].replace(':','')}.md"
    report_path.write_text(md, encoding="utf-8")
    print(f"  [FILE] 报告生成: {report_path} ({len(md)} bytes)")

    sumdb.close()
    print("AI-SUM 拉升前兆扫描完成\n")
    return results


if __name__ == "__main__":
    run()
