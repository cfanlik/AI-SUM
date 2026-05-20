#!/usr/bin/env python3
"""
pump_detector.py v2 (2026-05-10, 双轨与浓度升级 2026-05-19)
从 BubbleMap + Gecko + Meta + TokenHistory + Futures 五表计算 9 维 pump_readiness 评分，分级输出告警。
"""
import sqlite3
import statistics
from datetime import datetime
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
    D3 评分：真实用户吸筹浓度分 (满分 20 分)
    35 天时序 p90 水位为 40.0%，打满分 20 分。
    阈值区间细化，彻底规避低信噪假吸筹噪音导致的评分压制。
    """
    if concentration is None:
        return 0
    if concentration >= 40.0:
        return 20
    elif concentration >= 25.0:
        return 16
    elif concentration >= 15.0:
        return 12
    elif concentration >= 8.0:
        return 8
    elif concentration >= 3.0:
        return 4
    return 0


def get_macro_micro_score(avg_acc_score, avg_macro_score):
    """
    D6 评分：精英均分+大盘门控双轨制 (满分 10 分)
    - 精英均分 (avg_acc_score): 仅对已被标记为 ACC 吸筹钱包计算均分，衡量主力质量 (占打分主轴)。
    - 大盘分 (avg_macro_score): 所有非CEX/DEX/合约地址的均分，客观描绘项目基本面。
    - 门控修正：大盘分 < 44 分（代表低估或吸筹不充分）扣 3 分；≥ 53 分（强势共识）奖励 2 分（上限 10 分）。
    """
    if avg_acc_score is None or avg_acc_score <= 0:
        return 0

    # 1. 主轴精英打分
    if avg_acc_score >= 80:
        score = 8
    elif avg_acc_score >= 70:
        score = 6
    elif avg_acc_score >= 60:
        score = 4
    elif avg_acc_score >= 40:
        score = 2
    else:
        score = 0

    # 2. 大盘分门控修正
    if avg_macro_score is not None:
        if avg_macro_score < 44:
            score -= 3
        elif avg_macro_score >= 53:
            score += 2

    # 3. 约束分数区间在 [0, 10]
    return max(0, min(10, score))


def calc_pump_readiness(src, sumdb, token_address, token_symbol, scan_time):
    """计算单币 9 维拉升指数"""
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

        # D2 LP (20分)
        if reserve >= 500000: d2_score = 20
        elif reserve >= 100000: d2_score = 15
        elif reserve >= 50000: d2_score = 10
        elif reserve >= 10000: d2_score = 5

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
            # D1 量缩 (20分)
            if vol_24h < 1000:
                d1_score = 0
            elif vol_ratio <= 0.3: d1_score = 20
            elif vol_ratio <= 0.6: d1_score = 15
            elif vol_ratio <= 1.0: d1_score = 10
            elif vol_ratio <= 1.5: d1_score = 5

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

    if latest_bm:
        # 获取大盘指标和吸筹数据
        totals = src.execute("""
            SELECT
                SUM(CASE WHEN is_cex=0 AND is_dex=0 AND is_contract=0 THEN 1 ELSE 0 END) as real_user_count,
                SUM(CASE WHEN is_accumulating=1 THEN 1 ELSE 0 END) as acc_count,
                AVG(CASE WHEN is_accumulating=1 THEN acc_score ELSE NULL END) as avg_acc_score,
                AVG(CASE WHEN is_cex=0 AND is_dex=0 AND is_contract=0 THEN acc_score ELSE NULL END) as avg_macro_score
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
        # consec
        consec_rows = sumdb.execute("""
            SELECT consec_acc FROM token_history
            WHERE token_symbol = ? AND computed_date <= ?
            ORDER BY computed_date DESC LIMIT 1
        """, [token_symbol, scan_time[:10]]).fetchone()
        consec_acc = consec_rows["consec_acc"] if consec_rows else 0

        # D4 Meta (15分)
        if consec_acc >= 25: d4_score = 15
        elif consec_acc >= 15: d4_score = 12
        elif consec_acc >= 8: d4_score = 8
        elif consec_acc >= 3: d4_score = 4

        # D5 留存 (10分)
        th_row = sumdb.execute("""
            SELECT retention_7d FROM token_history
            WHERE token_symbol = ? AND computed_date <= ?
            ORDER BY computed_date DESC LIMIT 1
        """, [token_symbol, scan_time[:10]]).fetchone()
        if th_row and th_row["retention_7d"]:
            retention_7d = th_row["retention_7d"]
            if retention_7d >= 95: d5_score = 10
            elif retention_7d >= 85: d5_score = 8
            elif retention_7d >= 75: d5_score = 6
            elif retention_7d >= 60: d5_score = 4

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
        if oi_chg > 0.15 and vol_ratio <= 0.3 and d1_score == 20:
            d1_score = 10

        # D7 OI变化 (15分)
        if oi_usd >= 1000000:
            if oi_chg >= 0.10: d7_score = 15
            elif oi_chg >= 0.03: d7_score = 10
            elif oi_chg >= -0.05: d7_score = 5
        else:
            # OI不足$1M，减半
            if oi_chg >= 0.10: d7_score = 7
            elif oi_chg >= 0.03: d7_score = 5
            elif oi_chg >= -0.05: d7_score = 2

        # D8 FR (10分)
        if fr <= -0.0003: d8_score = 10
        elif fr <= -0.0001: d8_score = 8
        elif fr <= 0: d8_score = 5
        elif fr <= 0.0005: d8_score = 2

        # D9 L/S (8分)
        if ls <= 0.6: d9_score = 8
        elif ls <= 0.9: d9_score = 6
        elif ls <= 1.2: d9_score = 4
        elif ls <= 1.5: d9_score = 2

    total_score = d1_score + d2_score + d3_score + d4_score + d5_score + d6_score + d7_score + d8_score + d9_score

    # 分级
    level = "WATCH"
    if total_score >= 90:
        level = "IMMINENT"
    elif total_score >= 65:
        if vol_ratio <= 0.3:
            level = "READY_VOL_SHRINK"
        else:
            level = "READY"
    elif total_score >= 45:
        level = "WATCH"
    else:
        level = "NONE"

    return {
        "symbol": token_symbol, "addr": token_address,
        "score": total_score, "level": level,
        "d1": d1_score, "d2": d2_score, "d3": d3_score, "d4": d4_score,
        "d5": d5_score, "d6": d6_score, "d7": d7_score, "d8": d8_score, "d9": d9_score,
        "concentration": concentration, "vol_ratio": vol_ratio,
        "oi_usd": oi_usd, "oi_chg": oi_chg, "fr": fr, "ls": ls, "reserve": reserve,
        "avg_acc_score": avg_acc_score, "avg_macro_score": avg_macro_score
    }


def ensure_pump_alerts_table(sumdb):
    """创建 pump_alerts 表"""
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
             d1_score, d2_score, d3_score, d4_score, d5_score, d6_score, d7_score, d8_score, d9_score,
             concentration, vol_ratio, oi_usd, oi_chg, fr, ls, reserve_usd)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            scan_time, r["symbol"], r["addr"], r["score"], r["level"],
            r["d1"], r["d2"], r["d3"], r["d4"], r["d5"], r["d6"], r["d7"], r["d8"], r["d9"],
            r["concentration"], r["vol_ratio"], r["oi_usd"], r["oi_chg"], r["fr"], r["ls"], r["reserve"]
        ))
        saved += 1
    sumdb.commit()
    return saved


def generate_report(scan_time, results):
    """生成 Markdown 看板报告"""
    lines = [f"# 🚀 AI-SUM 拉升前兆引擎报告 (v2) — {scan_time}\n"]
    lines.append("> 基于 BubbleMap 真实吸筹浓度 + 大盘/精英双轨制 + Futures 链上与合约 9 维偏离扫描")
    lines.append("> 每 6 小时生成一次 (与 history_report.py 对齐)")
    lines.append("")

    lines.append("## 📖 报表说明 (Tips)")
    lines.append("- **pump**: 拉升就绪评分 (满分128). `IMMINENT` ≥90 (🔴) | `READY` ≥65 (🟡) | `WATCH` ≥45 (🟢)")
    lines.append("- **D1-D6**: 链上评分. D1量缩(20) | D2 LP(20) | D3真实吸筹浓度(20) | D4持续(15) | D5留存(10) | D6精英/大盘双轨(10)")
    lines.append("- **D7-D9**: 合约评分. D7 OI变化(15) | D8 资金费(10) | D9 多空比(8)")
    lines.append("- **吸筹%**: 真实吸筹浓度 (排除交易所/合约/CEX后的独立ACC地址占比)")
    lines.append("- **vol缩比**: 24h量/7d均量 (≤0.3x 为强力锁仓量缩)")
    lines.append("- **L/S**: 合约主动多空人数比 (<0.6 散户开空做反指)")
    lines.append("")

    def _fmtk(v):
        if v is None: return "—"
        if v >= 1e6: return f"${v/1e6:.1f}M"
        if v >= 1e3: return f"${v/1e3:.0f}K"
        return f"${v:.0f}"

    def _render_table(level_results, title, emoji):
        if not level_results:
            return ""
        table = [
            f"### {emoji} {title} ({len(level_results)} 个)",
            "",
            "| 代币 | pump | D1-D6 | D7 | D8 | D9 | 吸筹% | vol缩比 | OI($) | OI变化 | FR | L/S | LP |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
        ]
        for r in level_results:
            d16 = f"{r['d1']}+{r['d2']}+{r['d3']:.0f}+{r['d4']}+{r['d5']}+{r['d6']}"
            oi_usd_str = _fmtk(r['oi_usd'])
            reserve_str = _fmtk(r['reserve'])
            table.append(
                f"| **{r['symbol']}** | **{r['score']}** | {d16} | {r['d7']} | {r['d8']} | {r['d9']} "
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
    divergence = [r for r in results if r["score"] >= 65 and r["oi_chg"] < -0.10]
    if divergence:
        lines.append("## ⚠️ 链上/合约分歧检测 (高评分但合约退潮)")
        lines.append("| 代币 | pump评分 | 链上吸筹浓度 | vol缩比 | OI变化24h | LP | 含义 |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for r in divergence:
            lines.append(f"| {r['symbol']} | {r['score']} | {r['concentration']:.1f}% | {r['vol_ratio']:.2f}x | {r['oi_chg']:+.1%} | {_fmtk(r['reserve'])} | 链上高度建仓，但衍生品市场减仓离场 |")
        lines.append("")

    return "\n".join(lines)


def run(scan_time=None):
    if not scan_time:
        scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"AI-SUM 拉升前兆扫描开始 | {scan_time}")
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
    print(f"  [DB] 保存 {saved} 个告警代币")

    # 生成 Markdown 报告
    md = generate_report(scan_time, results)
    out_dir = Path(REPORT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"pump_{scan_time[:10].replace('-','')}_{scan_time[11:16].replace(':','')}.md"
    report_path.write_text(md, encoding="utf-8")
    print(f"  [FILE] 报告生成: {report_path} ({len(md)} bytes)")

    sumdb.close()
    print("AI-SUM 拉升前兆扫描完成\n")


if __name__ == "__main__":
    run()
