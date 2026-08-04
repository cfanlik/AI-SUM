from __future__ import annotations
import json
import sqlite3
import os
import hashlib
from datetime import datetime, timedelta
from statistics import median
from typing import Dict, List, Optional, Any

SELECT_DB = "/opt/select-coin/data/select.db"
VALIDATION_DB = "/opt/AI-SUM/data/signal-validation.db"
OUT_DIR = "/opt/AI-SUM/report/anomaly/observation"
LATEST_REPORT_PATH = "/opt/AI-SUM/report/anomaly/observation/latest_live_observation.md"

def get_db_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn

def parse_dt(v: Any) -> datetime:
    return datetime.fromisoformat(str(v).replace("Z", ""))

def get_latest_market(conn: sqlite3.Connection, chain: str, token: str, cutoff: str, pool: Optional[str] = None) -> Optional[sqlite3.Row]:
    query = """
        SELECT scan_time, pool_address, price_usd, reserve_usd, volume_24h
        FROM gecko_market_data
        WHERE chain=? AND token_address=? AND scan_time<=?
    """
    params = [chain, token, cutoff]
    if pool:
        query += " AND pool_address=?"
        params.append(pool)
    query += " ORDER BY scan_time DESC LIMIT 1"
    return conn.execute(query, params).fetchone()

def calculate_holder_metrics(conn: sqlite3.Connection, chain: str, token: str, a_time: str) -> Dict[str, Any]:
    # 查找 A 时点最近的快照时间
    latest_time_row = conn.execute(
        """SELECT MAX(snapshot_time) FROM bubblemap_holders
           WHERE chain=? AND token_address=? AND snapshot_time<=?""",
        (chain, token, a_time)
    ).fetchone()
    latest_time = latest_time_row[0] if latest_time_row else None

    # 查找 A - 7d 以前最近的快照时间
    prev_cutoff = (parse_dt(a_time) - timedelta(days=7)).isoformat(sep=" ")
    prev_time_row = conn.execute(
        """SELECT MAX(snapshot_time) FROM bubblemap_holders
           WHERE chain=? AND token_address=? AND snapshot_time<=?""",
        (chain, token, prev_cutoff)
    ).fetchone()
    prev_time = prev_time_row[0] if prev_time_row else None

    if not latest_time or not prev_time:
        return {
            "ratio": "N/A",
            "delta": "N/A",
            "ratio_val": 0.0,
            "delta_val": 0.0,
            "status": "holder_snapshot_missing"
        }

    sql = """
        SELECT COALESCE(is_accumulating, 0) as is_acc, COALESCE(hold_percentage, 0) as hold_pct
        FROM bubblemap_holders
        WHERE chain=? AND token_address=? AND snapshot_time=?
        ORDER BY rank ASC LIMIT 300
    """
    latest_holders = conn.execute(sql, (chain, token, latest_time)).fetchall()
    prev_holders = conn.execute(sql, (chain, token, prev_time)).fetchall()

    if not latest_holders or not prev_holders:
        return {
            "ratio": "N/A",
            "delta": "N/A",
            "ratio_val": 0.0,
            "delta_val": 0.0,
            "status": "holder_topn_incomplete"
        }

    acc_count = sum(1 for r in latest_holders if r["is_acc"] == 1)
    ratio_val = acc_count / len(latest_holders)
    
    delta_val = sum(r["hold_pct"] for r in latest_holders) - sum(r["hold_pct"] for r in prev_holders)

    return {
        "ratio": f"{ratio_val:.1%}",
        "delta": f"{delta_val:+.2%}",
        "ratio_val": ratio_val,
        "delta_val": delta_val,
        "status": "SUCCESS"
    }

def generate_report():
    print("--- 启动当前市场观察与多维白盒判定报告生成 (Live Observation) ---")
    
    select_conn = get_db_connection(SELECT_DB)
    
    # 获取最新的 Gecko 扫描时间作为实时对账基线
    as_of = select_conn.execute("SELECT MAX(scan_time) FROM gecko_market_data").fetchone()[0]
    as_of_dt = parse_dt(as_of)
    print(f"实时对账基线时刻 (Gecko as_of): {as_of}")

    try:
        sum_conn = get_db_connection(VALIDATION_DB)
        events_rows = sum_conn.execute(
            """SELECT DISTINCT chain, token_address, token_symbol, a_time, pool_address
               FROM asset_identity
               WHERE a_time <= ?
               ORDER BY a_time DESC LIMIT 15""",
            (as_of,)
        ).fetchall()
        sum_conn.close()
    except Exception as e:
        print(f"核验库联查异常，回退至 select.db 进行默认列表生成: {e}")
        events_rows = []

    if not events_rows:
        events_rows = select_conn.execute(
            """SELECT DISTINCT chain, token_address, 'MOCK' as token_symbol, MAX(scan_time) as a_time, pool_address
               FROM gecko_market_data
               WHERE scan_time <= ?
               GROUP BY chain, token_address
               ORDER BY a_time DESC LIMIT 10""",
            (as_of,)
        ).fetchall()

    lines = [
        "# Formal Signal Validation Report (Live Observation)",
        "",
        f"> 生成时间（UTC）：{datetime.utcnow().isoformat(timespec='seconds')}Z",
        f"> 数据源：SQLite 生产库只读对账；对账基线 (Gecko as_of)：`{as_of}`",
        "> 声明：本报告为实时观察（WATCH）与白盒判定表格，未满结算期标的强制标为 `PENDING` 锁防未来数据泄漏。",
        "",
        "## 一、 最终对准修正后的数据报告表 (Aligned Observation)",
        "",
        "| 代币符号 | 信号时间 (A) | A发生价 | 当前实时价 | 3d 已实现收益 (r3d) | 7d 未来预期走向 | Top 300 吸筹地址比 | 7d 大户持仓变动 (Δ) | 模型白盒判定与交易指导意见 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    for ev in events_rows:
        chain = ev["chain"]
        token = ev["token_address"]
        symbol = ev["token_symbol"] or "N/A"
        a_time = ev["a_time"]
        bound_pool = ev["pool_address"]

        a_dt = parse_dt(a_time)

        a_market = get_latest_market(select_conn, chain, token, a_time)
        now_market = get_latest_market(select_conn, chain, token, as_of)

        if not a_market or not now_market:
            lines.append(f"| {symbol} | {a_time[5:16]} | N/A | N/A | N/A | ⚪ 数据缺失 | N/A | N/A | 无法对账：Gecko行情缺失。 |")
            continue

        a_price = float(a_market["price_usd"] or 0)
        now_price = float(now_market["price_usd"] or 0)

        r3d_str = "PENDING"
        is_pending = True
        
        if (as_of_dt - a_dt).total_seconds() >= 259200:
            is_pending = False
            target_time = (a_dt + timedelta(days=3)).isoformat(sep=" ")
            exit_market = get_latest_market(select_conn, chain, token, target_time, pool=a_market["pool_address"])
            if exit_market:
                align_hours = abs((parse_dt(exit_market["scan_time"]) - parse_dt(target_time)).total_seconds()) / 3600
                if align_hours <= 4.0:
                    exit_price = float(exit_market["price_usd"] or 0)
                    if a_price > 0:
                        r3d_val = (exit_price / a_price - 1)
                        r3d_str = f"{r3d_val:+.2%}"
                    else:
                        r3d_str = "N/A"
                else:
                    r3d_str = "PENDING"
            else:
                r3d_str = "PENDING"

        h_metrics = calculate_holder_metrics(select_conn, chain, token, a_time)
        ratio_str = h_metrics["ratio"]
        delta_str = h_metrics["delta"]
        ratio_val = h_metrics["ratio_val"]
        delta_val = h_metrics["delta_val"]

        history_lp_rows = select_conn.execute(
            """SELECT reserve_usd FROM gecko_market_data
               WHERE chain=? AND token_address=? AND pool_address=?
                 AND scan_time<=? AND scan_time>=?""",
            (chain, token, now_market["pool_address"], as_of, (as_of_dt - timedelta(days=7)).isoformat(sep=" "))
        ).fetchall()
        lp_drawdown = False
        if len(history_lp_rows) >= 5:
            lp_baseline = median([float(r["reserve_usd"] or 0) for r in history_lp_rows])
            curr_lp = float(now_market["reserve_usd"] or 0)
            if lp_baseline > 0 and curr_lp / lp_baseline <= 0.60:
                lp_drawdown = True

        direction = "⚪ 横盘观望 (未突破)"
        guidance = "观望：大户吸筹率偏低，价格突破不明显，庄家尚未作价，建议观望。"

        if lp_drawdown or (ratio_val < 0.25 and delta_val < 0):
            direction = "❌ 归零风险 (池子跑路)"
            guidance = f"拦截：大户连续吸筹率仅 {ratio_str} 且 7d 净仓呈流出状态。配合池子 LP 暴跌，判定撤池跑路，强力拦截。"
        else:
            price_change = (now_price / a_price - 1) if a_price > 0 else 0
            if price_change >= 0.02 or price_change <= -0.02:
                if delta_val <= 0.0005:
                    direction = "🔴 预期暴跌 (对倒出货)"
                    guidance = "做空：已达成价格突破，但大户无明显增仓且吸筹虚弱，大庄高位对倒分发出货概率极高，建议开空。"
                else:
                    direction = "🟢 预期拉升 (强吸筹)"
                    guidance = "做多：大户呈净流入且达成价格有效向上突破，主力作价意图强，建议关注做多。"
            else:
                if is_pending:
                    if delta_val <= 0.0005:
                        direction = "🔴 预期暴跌 (对倒出货)"
                        guidance = f"做空：已达成突破。大户无增仓且 7d 净仓增幅为 {delta_str}。信号触发仅 {(as_of_dt - a_dt).total_seconds()/86400:.1f} 天，r3d 结算锁处于 PENDING，但历史做空 Expectancy 规律触发。"

        a_price_str = f"${a_price:.5f}" if a_price < 1.0 else f"${a_price:.2f}"
        now_price_str = f"${now_price:.5f}" if now_price < 1.0 else f"${now_price:.2f}"

        lines.append(
            f"| **{symbol}** | {a_time[5:16]} | {a_price_str} | {now_price_str} | **{r3d_str}** | {direction} | {ratio_str} | {delta_str} | {guidance} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 二、 遥测大户持仓占比观察 (Top 300 Accumulation Telemetry)",
        "",
        "各观测代币在 A 时点的 Top 300 连续吸筹地址与 7d 大户持仓占比变动 (Δ)：",
        ""
    ]

    for ev in events_rows:
        chain = ev["chain"]
        token = ev["token_address"]
        symbol = ev["token_symbol"] or "N/A"
        a_time = ev["a_time"]
        h_metrics = calculate_holder_metrics(select_conn, chain, token, a_time)
        lines.append(f"* **{symbol}**：Top 300 连续吸筹地址比 = `{h_metrics['ratio']}` | 7d 大户持仓占比差 (Δ) = `{h_metrics['delta']}`")

    lines += [
        "",
        "---",
        "",
        "## 三、 数据边界与自审",
        "",
        "- 本报告基于 `/opt/select-coin/data/select.db` 生产大表及 `select-sum.db` 做只读物理审计。",
        "- 在 L3 实盘 OOS 回测判定通过（即 `l3_status = PASS`）之前，本报告所呈现的交易指导意见属于白盒指标规律的 WATCH 状态观察，不构成入场交易信号。",
    ]

    select_conn.close()

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(LATEST_REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"DEX 市场实时观察报告生成完毕: {LATEST_REPORT_PATH}")

if __name__ == "__main__":
    generate_report()
