#!/usr/bin/env python3
"""
pump_detector.py v4 (2026-06-02, 11维100分权重体系 & 外挂融合版)
从 BubbleMap + Gecko + Meta + TokenHistory + Futures + DEX_Pool_Volatility 六表计算 11 维 pump_readiness 评分，分级输出告警。

v4 变更:
  - 融合外挂 `dex_pool_analyzer.py` 的多尺度变动指标
  - 新增 D11 DEX LP 波动共振分 (满分 5 分，添加 LP >= 15% 强庄注入得 5分)
  - 评分权重微调：D1量缩(13) | D2 LP规模(10) | D3 浓度(14) | D4 Meta持续(9) | D5 留存(6) |
                 D6 双轨(6) | D7 OI变化(10) | D8 FR(7) | D9 LS(5) | D10 Pool稳定(7) | D11 LP变动(5)
  - 在报告头部增设「🚨 撤池 Rugpull 风险高危雷达」，置顶警示 S5 (大撤池) 高危代币
  - 在报告中融合 24h/48h/72h 多尺度 LP 水位与添加占比 (Add%) 看板
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
    c = sqlite3.connect(uri, uri=readonly, timeout=60.0)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout = 60000;")
    return c


def get_acc_concentration_score(concentration):
    """D3 评分：真实用户吸筹浓度分 (满分 15 分)"""
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
    """D6 评分：精英均分+大盘门控双轨制 (满分 6 分)"""
    if avg_acc_score is None or avg_acc_score <= 0:
        return 0

    if avg_acc_score >= 80:
        score = 5
    elif avg_acc_score >= 70:
        score = 3
    elif avg_acc_score >= 60:
        score = 2
    elif avg_acc_score >= 40:
        score = 1
    else:
        score = 0

    if avg_macro_score is not None:
        if avg_macro_score < 44:
            score -= 1
        elif avg_macro_score >= 53:
            score += 1

    return max(0, min(6, score))


def get_pool_stability_score(src, token_address, scan_time=None):
    """
    D10 评分：Pool 稳定性 (满分 8 分)
    基于 gecko_market_data 近 7 天 reserve_usd 时序 data。

    子项:
      - LP 7d 波动率 (max 6): 极稳(<3%)→6, 稳(<8%)→5, 正常(<15%)→3, 波动(<25%)→1
      - 交易活跃度 (max 2): ≥500txns→2, ≥100→1

    返回: (d10_score, lp_volatility_pct, reserve_latest, txns_24h)
    """
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    if scan_time:
        # 从 scan_time 解析并锁定 7 天历史区间
        from datetime import datetime as dt
        try:
            dt_scan = dt.fromisoformat(scan_time.replace("+00:00", "").replace("Z", ""))
        except Exception:
            try:
                dt_scan = dt.strptime(scan_time, "%Y-%m-%d %H:%M:%S")
            except Exception:
                dt_scan = dt.now()
        week_ago = (dt_scan - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        rows = src.execute("""
            SELECT reserve_usd, buys_24h, sells_24h
            FROM gecko_market_data
            WHERE token_address = ? AND scan_time >= ? AND scan_time <= ?
            ORDER BY scan_time DESC
        """, [token_address, week_ago, scan_time]).fetchall()
    else:
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

    # 子项1: LP 波动率 (max 6)（已移除 LP 规模 2 分重复计权，转移至此项）
    lp_vol_score = 0
    lp_vol_pct = None
    if len(reserves) >= 2:
        r_max, r_min = max(reserves), min(reserves)
        lp_vol_pct = round((r_max - r_min) / r_max * 100, 1) if r_max > 0 else 100
        if lp_vol_pct < 3:
            lp_vol_score = 6
        elif lp_vol_pct < 8:
            lp_vol_score = 5
        elif lp_vol_pct < 15:
            lp_vol_score = 3
        elif lp_vol_pct < 25:
            lp_vol_score = 1

    # 子项3: 交易活跃度 (max 2)
    activity_score = 0
    if txns >= 500:
        activity_score = 2
    elif txns >= 100:
        activity_score = 1

    d10_total = lp_vol_score + activity_score
    return d10_total, lp_vol_pct, reserve_latest, txns


def get_dex_lp_resonance_score(src, token_address, scan_time=None):
    """
    D11 评分：DEX LP 深度波动共振 (满分 5 分)
    直接读取外挂分析器计算落库在 pool_volatility_snapshots 中的最新多尺度数据。
    """
    if scan_time:
        row = src.execute("""
            SELECT lp_chg_24h, add_ratio_24h, whale_share_24h, a5_triggered, s5_triggered, reason
            FROM pool_volatility_snapshots
            WHERE token_address = ? AND scan_time <= ?
            ORDER BY scan_time DESC LIMIT 1
        """, [token_address, scan_time]).fetchone()
    else:
        row = src.execute("""
            SELECT lp_chg_24h, add_ratio_24h, whale_share_24h, a5_triggered, s5_triggered, reason
            FROM pool_volatility_snapshots
            WHERE token_address = ?
            ORDER BY scan_time DESC LIMIT 1
        """, [token_address]).fetchone()
    
    if not row:
        return 0, 0.0, 0.0, 0.0, False, False, ""
        
    lp_chg = row["lp_chg_24h"] or 0.0
    add_ratio = row["add_ratio_24h"] or 0.0
    whale_share = row["whale_share_24h"] or 0.0
    a5 = bool(row["a5_triggered"])
    s5 = bool(row["s5_triggered"])
    reason = row["reason"] or ""
    
    # 评分模型
    d11_score = 0
    if a5:
        d11_score = 5
    elif lp_chg >= 0.05 and add_ratio >= 0.70:
        d11_score = 3
    elif lp_chg >= 0.0:
        d11_score = 1
        
    return d11_score, lp_chg, add_ratio, whale_share, a5, s5, reason


def calc_pump_readiness(src, sumdb, token_address, token_symbol, scan_time):
    """计算单币 11 维拉升指数 (满分 100)"""
    addr = token_address.lower() if token_address else ""

    # D1 & D2: gecko 交易量与 LP (限定 scan_time 实现回测对齐)
    latest_gecko = src.execute("""
        SELECT volume_24h, reserve_usd, price_change_24h
        FROM gecko_market_data WHERE token_address = ? AND scan_time <= ?
        ORDER BY scan_time DESC LIMIT 1
    """, [token_address, scan_time]).fetchone()

    d1_score = 0
    d2_score = 0
    vol_ratio = 1.0
    reserve = 0
    vol_24h = 0
    price_chg = 0

    if latest_gecko:
        vol_24h = latest_gecko["volume_24h"] or 0
        # 防御性上限截断（防范 RLS 等 21.8 亿 LP 数据污染）
        reserve = min(latest_gecko["reserve_usd"] or 0, 50_000_000)
        price_chg = latest_gecko["price_change_24h"] or 0

        # D2 LP 规模 (12分)
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
                WHERE token_address = ? AND volume_24h > 0 AND scan_time <= ?
                ORDER BY scan_time DESC LIMIT 14
            )
        """, [token_address, scan_time]).fetchone()[0] or 0

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
            # 激活 price_chg 作为 D1 量缩得分的修正因子
            if price_chg < -5.0 and vol_ratio <= 0.5:
                # 缩量下跌说明筹码恐慌出清完毕，加 2 分
                d1_score = min(15, d1_score + 2)
            elif price_chg > 10.0 and vol_ratio <= 0.5:
                # 缩量上涨说明已经开始拉升，调降前兆分 3 分
                d1_score = max(0, d1_score - 3)

    # D3 & D6: 真实浓度与大盘/精英双轨制 (限定 snapshot_time <= scan_time)
    latest_bm = src.execute("""
        SELECT MAX(snapshot_time) FROM bubblemap_holders WHERE token_address = ? AND snapshot_time <= ?
    """, [token_address, scan_time]).fetchone()[0]

    concentration = 0
    d3_score = 0
    d6_score = 0
    real_users = 0
    acc_count = 0
    avg_acc_score = 0
    avg_macro_score = 0
    control_level = "None"

    associated_addresses = 0
    associated_ratio = 0.0
    if latest_bm:
        totals = src.execute("""
            SELECT
                SUM(CASE WHEN is_cex=0 AND is_dex=0 AND is_contract=0 THEN 1 ELSE 0 END) as real_user_count,
                SUM(CASE WHEN is_accumulating=1 THEN 1 ELSE 0 END) as acc_count,
                AVG(CASE WHEN is_accumulating=1 THEN acc_score ELSE NULL END) as avg_acc_score,
                AVG(CASE WHEN is_cex=0 AND is_dex=0 AND is_contract=0 THEN acc_score ELSE NULL END) as avg_macro_score,
                MAX(control_level) as max_control,
                COALESCE(MAX(associated_addresses), 0) as associated_addresses,
                COALESCE(MAX(associated_ratio), 0.0) as associated_ratio
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
            associated_addresses = totals["associated_addresses"] or 0
            associated_ratio = totals["associated_ratio"] or 0.0
            
            if control_level in ("A", "B"):
                d3_score = 15

    # 爆破计算
    underwater_ratio = 0.0
    latest_gmgn = src.execute("""
        SELECT MAX(snapshot_time) FROM gmgn_holders WHERE token_address = ? AND snapshot_time <= ?
    """, [token_address, scan_time]).fetchone()[0]
    
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

    stealth_pump = False
    if control_level in ("A", "B") and underwater_ratio > 0.90:
        stealth_pump = True

    # D4 & D5: Meta与留存
    latest_meta = sumdb.execute("""
        SELECT meta_score, meta_verdict FROM meta_snapshots
        WHERE token_symbol = ? AND scan_time <= ?
        ORDER BY scan_time DESC LIMIT 1
    """, [token_symbol, scan_time]).fetchone()

    d4_score = 0
    d5_score = 0
    consec_acc = 0
    retention_7d = 0

    # D4 Meta持续 (10分) — 保持在 ACC 嵌套内，consec_acc 定义依赖 ACC 状态
    if latest_meta and latest_meta["meta_verdict"] == "ACC":
        consec_rows = sumdb.execute("""
            SELECT consec_acc FROM token_history
            WHERE token_symbol = ? AND computed_date <= ?
            ORDER BY computed_date DESC LIMIT 1
        """, [token_symbol, scan_time[:10]]).fetchone()
        consec_acc = consec_rows["consec_acc"] if consec_rows else 0

        # D4 Meta持续 (9分)
        if consec_acc >= 25:
            d4_score = 9
        elif consec_acc >= 15:
            d4_score = 7
        elif consec_acc >= 8:
            d4_score = 4
        elif consec_acc >= 3:
            d4_score = 2

    # D5 留存 (6分) — 剥离 ACC 嵌套，作为链上客观吸筹指标独立计算
    th_row = sumdb.execute("""
        SELECT retention_7d FROM token_history
        WHERE token_symbol = ? AND computed_date <= ?
        ORDER BY computed_date DESC LIMIT 1
    """, [token_symbol, scan_time[:10]]).fetchone()
    if th_row and th_row["retention_7d"]:
        retention_7d = th_row["retention_7d"]
        if retention_7d >= 95:
            d5_score = 6
        elif retention_7d >= 85:
            d5_score = 4
        elif retention_7d >= 75:
            d5_score = 3
        elif retention_7d >= 60:
            d5_score = 2

    # D7, D8, D9: futures (限定 scan_time 时间)
    latest_ft = src.execute("""
        SELECT oi_value_usd, oi_change_24h, funding_rate, long_short_ratio
        FROM futures_snapshots WHERE token_address = ? AND scan_time <= ?
        ORDER BY scan_time DESC LIMIT 1
    """, [token_address, scan_time]).fetchone()

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
        # 优化：增加例外条件以防止误伤轧空标的。仅当代币为死币 (vol_24h < 1000) 或非轧空状态 (fr >= 0) 时才扣分
        if oi_chg > 0.15 and vol_ratio <= 0.3 and d1_score == 15:
            if vol_24h < 1000 or fr >= 0:
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

        # D8 FR (7分)
        # 优化：修正语义不自洽。fr <= 0 才计分，正资金费率得分归零
        if fr <= -0.0003:
            d8_score = 7
        elif fr <= -0.0001:
            d8_score = 5
        elif fr <= 0:
            d8_score = 3
        else:
            d8_score = 0

        # D9 L/S (5分)
        if ls <= 0.6:
            d9_score = 5
        elif ls <= 0.9:
            d9_score = 3
        elif ls <= 1.2:
            d9_score = 2

    # D10 Pool 稳定性 (8分)
    d10_score, lp_vol_pct, _, d10_txns = get_pool_stability_score(src, token_address, scan_time)

    # 🛠️ 融合同步：D11 DEX LP 波动共振得分 (5分)
    d11_score, lp_chg_24h, add_ratio_24h, whale_share_24h, a5_triggered, s5_triggered, d11_reason = get_dex_lp_resonance_score(src, token_address, scan_time)

    total_score = (d1_score + d2_score + d3_score + d4_score + d5_score
                   + d6_score + d7_score + d8_score + d9_score + d10_score + d11_score)

    # OI/LP 杠杆轧空检测 (高持仓额远超现货LP深度且资金费率为负)
    high_leverage_squeeze = False
    if latest_ft and oi_usd >= 500000 and reserve > 0:
        if (oi_usd / reserve) >= 10.0 and fr < 0:
            high_leverage_squeeze = True

    # 双轨阈值分流评级，解决无合约偏见，并适配不可达阈值修复 (IMMINENT 70->62, READY 50->48)
    level = "WATCH"
    if latest_ft is not None:
        # 1. 有合约代币评级体系
        if total_score >= 62:
            level = "IMMINENT"
        elif total_score >= 48:
            if vol_ratio <= 0.3:
                level = "READY_VOL_SHRINK"
            else:
                level = "READY"
        elif total_score >= 35:
            level = "WATCH"
        else:
            level = "NONE"
    else:
        # 2. 无合约代币评级体系 (最高上限为 79 分 = 105 - 26)
        if total_score >= 56:
            level = "IMMINENT"
        elif total_score >= 40:
            if vol_ratio <= 0.3:
                level = "READY_VOL_SHRINK"
            else:
                level = "READY"
        elif total_score >= 28:
            level = "WATCH"
        else:
            level = "NONE"

    # S11 静默建仓信号 (放宽条件至 3-of-4)
    silent_acc = False
    try:
        # 条件1: LP 7d波动率 < 5%
        lp_stable = lp_vol_pct is not None and lp_vol_pct < 5
        
        # 条件2: 合约 OI 近 3 个快照持续增长
        ft_rows = src.execute("""
            SELECT oi_value_usd, funding_rate FROM futures_snapshots
            WHERE LOWER(token_address)=LOWER(?) ORDER BY scan_time DESC LIMIT 3
        """, [token_address]).fetchall()
        oi_growing = len(ft_rows) == 3 and ft_rows[0]["oi_value_usd"] > ft_rows[2]["oi_value_usd"]
        
        # 条件3: 最新 FR 为负
        fr_negative = len(ft_rows) > 0 and (ft_rows[0]["funding_rate"] or 0) < 0
        
        # 条件4: 链上吸筹均分近 4 个快照呈上升趋势
        snap_rows = src.execute("""
            SELECT AVG(CASE WHEN is_accumulating=1 THEN acc_score END) as avg_score
            FROM bubblemap_holders
            WHERE LOWER(token_address)=LOWER(?)
            GROUP BY snapshot_time ORDER BY snapshot_time DESC LIMIT 4
        """, [token_address]).fetchall()
        scores = [r["avg_score"] for r in snap_rows if r["avg_score"]]
        score_rising = len(scores) >= 2 and scores[0] > scores[-1]
        
        # 4条件中满足3条即认定为静默建仓
        conditions = [lp_stable, oi_growing, fr_negative, score_rising]
        if sum(1 for c in conditions if c) >= 3:
            silent_acc = True
    except Exception as _e:
        pass

    return {
        "symbol": token_symbol, "addr": token_address,
        "score": total_score, "level": level,
        "d1": d1_score, "d2": d2_score, "d3": d3_score, "d4": d4_score,
        "d5": d5_score, "d6": d6_score, "d7": d7_score, "d8": d8_score,
        "d9": d9_score, "d10": d10_score, "d11": d11_score,
        "concentration": concentration, "vol_ratio": vol_ratio,
        "oi_usd": oi_usd, "oi_chg": oi_chg, "fr": fr, "ls": ls, "reserve": reserve,
        "avg_acc_score": avg_acc_score, "avg_macro_score": avg_macro_score,
        "lp_vol_pct": lp_vol_pct, "silent_acc": silent_acc,
        "underwater_ratio": round(underwater_ratio, 4),
        "control_level": control_level,
        "stealth_pump": stealth_pump,
        "associated_addresses": associated_addresses,
        "associated_ratio": associated_ratio,
        "high_leverage_squeeze": high_leverage_squeeze,
        "has_ft": latest_ft is not None,
        
        # D11 独立外挂指标集成
        "lp_chg_24h": lp_chg_24h,
        "add_ratio_24h": add_ratio_24h,
        "whale_share_24h": whale_share_24h,
        "a5_triggered": a5_triggered,
        "s5_triggered": s5_triggered,
        "d11_reason": d11_reason
    }


def ensure_pump_alerts_table(sumdb):
    """创建 pump_alerts 表 + v4 自动升级"""
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
            d10_score       REAL DEFAULT 0,
            d11_score       REAL DEFAULT 0,
            concentration   REAL DEFAULT 0,
            vol_ratio       REAL DEFAULT 0,
            oi_usd          REAL DEFAULT 0,
            oi_chg          REAL DEFAULT 0,
            fr              REAL DEFAULT 0,
            ls              REAL DEFAULT 0,
            reserve_usd     REAL DEFAULT 0,
            silent_acc      INTEGER DEFAULT 0,
            underwater_ratio REAL DEFAULT 0,
            control_level   TEXT DEFAULT 'None',
            stealth_pump    INTEGER DEFAULT 0,
            UNIQUE(scan_time, token_address)
        )
    """)
    # v3 升级: 新增 d10_score / silent_acc 列
    existing = {row[1] for row in sumdb.execute("PRAGMA table_info(pump_alerts)").fetchall()}
    v3_cols = {"d10_score": "REAL DEFAULT 0", "silent_acc": "INTEGER DEFAULT 0"}
    for col_name, col_def in v3_cols.items():
        if col_name not in existing:
            sumdb.execute(f"ALTER TABLE pump_alerts ADD COLUMN {col_name} {col_def}")
            
    # v4 升级: 新增 underwater_ratio / control_level / stealth_pump / high_leverage_squeeze 列
    v4_cols = {
        "underwater_ratio": "REAL DEFAULT 0",
        "control_level": "TEXT DEFAULT 'None'",
        "stealth_pump": "INTEGER DEFAULT 0",
        "high_leverage_squeeze": "INTEGER DEFAULT 0"
    }
    for col_name, col_def in v4_cols.items():
        if col_name not in existing:
            sumdb.execute(f"ALTER TABLE pump_alerts ADD COLUMN {col_name} {col_def}")
    sumdb.commit()
    try:
        sumdb.execute("ALTER TABLE pump_alerts ADD COLUMN d11_score REAL DEFAULT 0;")
        sumdb.commit()
    except Exception:
        pass


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
             d7_score, d8_score, d9_score, d10_score, d11_score,
             concentration, vol_ratio, oi_usd, oi_chg, fr, ls, reserve_usd, silent_acc,
             underwater_ratio, control_level, stealth_pump, high_leverage_squeeze)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            scan_time, r["symbol"], r["addr"], r["score"], r["level"],
            r["d1"], r["d2"], r["d3"], r["d4"], r["d5"], r["d6"],
            r["d7"], r["d8"], r["d9"], r["d10"], r["d11"],
            r["concentration"], r["vol_ratio"], r["oi_usd"], r["oi_chg"],
            r["fr"], r["ls"], r["reserve"],
            1 if r.get("silent_acc") else 0,
            r.get("underwater_ratio", 0.0),
            r.get("control_level", "None"),
            1 if r.get("stealth_pump") else 0,
            1 if r.get("high_leverage_squeeze") else 0,
        ))
        saved += 1
    sumdb.commit()
    return saved


def generate_report(scan_time, results):
    """生成 Markdown 看板报告"""
    lines = [f"# 🚀 AI-SUM 拉升前兆与流动性共振报告 (v4) — {scan_time}\n"]
    lines.append("> 基于 BubbleMap + Gecko + Futures + DEX LP 时序差分 11 维评分体系 (满分 100)")
    lines.append("> 融入强庄做市 (A5) 与撤池高危 (S5) 双向流向评估算法")
    lines.append("")

    # ─── 🚨 撤池 Rugpull 风险高危雷达 (置顶警示) ───
    drain_tokens = [r for r in results if r.get("s5_triggered")]
    lines.append("## 🚨 撤池 Rugpull 风险高危雷达")
    if drain_tokens:
        lines.append("> ⚠️ **高危警告**：检测到主力或做市商大额撤出流动性 (LP 负波动)，抽毯子概率极大，严禁追高！")
        lines.append("")
        lines.append("| 代币 | 24h LP 变动 | 移除占比 (Remove%) | 庄控级别 | pump评分 | 警报类型 |")
        lines.append("| :--- | :---: | :---: | :---: | :---: | :--- |")
        for r in drain_tokens:
            lines.append(
                f"| **{r['symbol']}** | **{r['lp_chg_24h'] * 100:+.1f}%** | {100.0 - r['add_ratio_24h'] * 100:.1f}% "
                f"| `{r['control_level']}` | **{r['score']}** | `🚨 {r['d11_reason']}` |"
            )
    else:
        lines.append("> [!NOTE]")
        lines.append("> 目前暂无代币触发 S5 撤池流失风险，DEX 流动性池运转正常。")
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
        lines.append("> 当检测到 A/B 级强庄控且大户深套率 `underwater_ratio > 90%` 时，触发爆破预警。目前暂无代币触发。")
    lines.append("")

    # ─── 💎 DEX LP 强庄做市与注入雷达 ───
    a5_tokens = [r for r in results if r.get("a5_triggered")]
    lines.append("## 💎 DEX LP 强庄做市与注入雷达 (A5)")
    if a5_tokens:
        lines.append("> 📈 **强力建仓**：做市商或大户正向大额注入流动性，且大户贡献集中度极高，拉升概率剧增！")
        lines.append("")
        lines.append("| 代币 | 24h LP 变动 | 注入占比 (Add%) | 大户份额 | 庄控级别 | pump评分 | 🤫 |")
        lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: |")
        for r in a5_tokens:
            silent_flag = "🤫" if r.get("silent_acc") else ""
            lines.append(
                f"| **{r['symbol']}** | **{r['lp_chg_24h'] * 100:+.1f}%** | {r['add_ratio_24h'] * 100:.1f}% "
                f"| {r['whale_share_24h'] * 100:.1f}% | `{r['control_level']}` | **{r['score']}** | {silent_flag} |"
            )
    else:
        lines.append("> [!NOTE]")
        lines.append("> 目前暂无代币触发 A5 强庄 LP 注入信号，庄家筹码尚在吸筹收尾中。")
    lines.append("")

    # ─── 🔗 BUBBLEMAP 庄控打款关联看板 (穿透) ───
    control_tokens = [r for r in results if r.get("control_level") in ("A", "B", "C")]
    lines.append("## 🔗 BUBBLEMAP 庄控打款关联看板 (穿透)")
    if control_tokens:
        lines.append("")
        lines.append("| 代币 | pump评分 | 庄控级别 | 关联持仓占比 | 关联地址数 | 浓度分(D3) | vol缩比(D1) | 🤫 |")
        lines.append("| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |")
        for r in control_tokens:
            silent_flag = "🤫" if r.get("silent_acc") else ""
            lines.append(
                f"| **{r['symbol']}** | **{r['score']}** | `{r.get('control_level', 'None')}级庄控` "
                f"| {r.get('associated_ratio', 0.0):.2f}% | {r.get('associated_addresses', 0)} | {r['d3']} | {r['vol_ratio']:.2f}x | {silent_flag} |"
            )
    lines.append("")

    lines.append("## 📖 报表说明 (Tips)")
    lines.append("- **pump**: 拉升就绪评分. 有合约代币: `IMMINENT` ≥62 (🔴) | `READY` ≥48 (🟡) | `WATCH` ≥35 (🟢). 纯现货代币: `IMMINENT` ≥56 (🔴) | `READY` ≥40 (🟡) | `WATCH` ≥28 (🟢)")
    lines.append("- **D1-D6**: 链上评分 (合计 66 分). D1量缩(15) | D2 LP规模(12) | D3吸筹浓度(15) | D4 Meta持续(10) | D5留存(7) | D6双轨(7)")
    lines.append("- **D7-D9**: 合约评分 (合计 26 分). D7 OI变化(12) | D8 资金费率(8) | D9 多空比(6) (无合约现货代币此三项为 0)")
    lines.append("- **D10**: Pool 稳定性 (8 分). LP 7d波动率(6) + 交易活跃度(2) (已剔除 LP 规模分以消除重复计权)")
    lines.append("- **D11**: DEX LP 波动共振 (5 分). 外挂分析器提取的 24h 时序差分与大户流向评分")
    lines.append("- **🤫**: S11 静默建仓信号 — LP极稳 + OI增长 + FR负 + 链上吸筹升 (4中3即触发)")
    lines.append("- **⚡**: 杠杆轧空预警 — 合约持仓额大于现货LP 10倍以上且资金费率为负")
    lines.append("- **🔗 纯链上**: 未在衍生品合约交易所上线的纯现货代币 (实行独立评分及告警阈值分轨)")
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
            "| 代币 | pump | D1-D6 | D7-D9 | D10 | D11 | 🤫 | DEX LP (24h) | Add% | 吸筹% | vol缩比 | OI($) | FR | LP |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :---: | :---: | :--- | :--- | :--- | :--- | :--- |"
        ]
        for r in level_results:
            d16 = f"{r['d1']}+{r['d2']}+{r['d3']:.0f}+{r['d4']}+{r['d5']}+{r['d6']}"
            d79 = f"{r['d7']}+{r['d8']}+{r['d9']}"
            oi_usd_str = _fmtk(r['oi_usd'])
            reserve_str = _fmtk(r['reserve'])
            
            flags = []
            if r.get("s5_triggered"):
                flags.append("🚨 撤池")
            elif r.get("a5_triggered"):
                flags.append("💎 强做市")
            elif r.get("stealth_pump"):
                flags.append("🤫")
            elif r.get("silent_acc"):
                flags.append("🤫")
            silent_flag = " / ".join(flags) if flags else ""
            
            table.append(
                f"| **{r['symbol']}** | **{r['score']}** | {d16} | {d79} | {r['d10']} | {r['d11']} | {silent_flag} "
                f"| {r['lp_chg_24h'] * 100:+.1f}% | {r['add_ratio_24h'] * 100:.1f}% "
                f"| {r['concentration']:.1f}% | {r['vol_ratio']:.2f}x | {oi_usd_str} | {r['fr']:.4%} | {reserve_str} |"
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

    return "\n".join(lines)


def run(scan_time=None):
    if not scan_time:
        scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"AI-SUM 拉升前兆扫描开始 (v4, 11维100分外挂集成版) | {scan_time}")
    src = connect(SRC_DB, readonly=True)
    sumdb = connect(SUM_DB)

    ensure_pump_alerts_table(sumdb)

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

    results.sort(key=lambda x: x["score"], reverse=True)

    saved = save_pump_alerts(sumdb, scan_time, results)
    silent_cnt = sum(1 for r in results if r.get("silent_acc"))
    print(f"  [DB] 保存 {saved} 个告警代币, 其中 S11 静默建仓 {silent_cnt} 个")

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
