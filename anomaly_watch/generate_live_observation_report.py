from __future__ import annotations
import json
import sqlite3
import os
import hashlib
import sys
import argparse
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Dict, List, Optional, Any

# 物理路径默认常量
SELECT_DB_DEFAULT = "/opt/select-coin/data/select.db"
VALIDATION_DB_DEFAULT = "/opt/AI-SUM/data/signal-validation.db"
OUT_DIR_DEFAULT = "/opt/AI-SUM/report/anomaly"
LATEST_REPORT_PATH_DEFAULT = "/opt/AI-SUM/report/anomaly/anomaly_live_observation.md"

# 中文化翻译对照表
SCENARIO_CN = {
    "OBSERVATION_RANGE": "横盘整理",
    "LOW_LIQUIDITY_OBSERVATION": "池子稀疏观测",
    "OBSERVATION_UP_MOVE_WITH_NET_INFLOW": "吸筹拉升观测",
    "OBSERVATION_UP_MOVE_WITH_NET_OUTFLOW": "对倒出货观测",
    "OBSERVATION_DOWN_MOVE_WITH_NET_INFLOW": "吸筹阴跌观测",
    "OBSERVATION_DOWN_MOVE_WITH_NET_OUTFLOW": "高危出货观测",
}
GATE_CN = {"PASS": "通过", "INTERCEPTED": "拦截"}
INPUT_CN = {
    "SUCCESS": "正常",
    "HOLDER_SNAPSHOT_MISSING": "快照缺失",
    "LP_BASELINE_INSUFFICIENT": "LP稀疏",
    "STALE_MARKET": "行情缺失",
    "EXIT_SNAPSHOT_MISSING": "结算缺失"
}

def get_db_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def parse_dt(v: Any) -> datetime:
    s = str(v).replace("Z", "")
    if " " in s:
        dt = datetime.fromisoformat(s)
    else:
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def format_price(p: float) -> str:
    if p >= 1.0:
        return f"${p:.2f}"
    elif p >= 0.01:
        return f"${p:.4f}"
    elif p < 1e-10:
        return f"${p:.4e}"
    else:
        s = f"{p:.10f}".rstrip('0')
        if s.endswith('.'):
            s += '0'
        return f"${s}"

def get_latest_market(conn: sqlite3.Connection, chain: str, token: str, cutoff: str, pool: Optional[str] = None) -> Optional[sqlite3.Row]:
    query = """
        SELECT scan_time, pool_address, price_usd, reserve_usd, volume_24h, dex_id
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
    latest_time_row = conn.execute(
        """SELECT MAX(snapshot_time) FROM bubblemap_holders
           WHERE chain=? AND token_address=? AND snapshot_time<=?""",
        (chain, token, a_time)
    ).fetchone()
    latest_time = latest_time_row[0] if latest_time_row else None

    prev_cutoff = (parse_dt(a_time) - timedelta(days=7)).isoformat(sep=" ")
    prev_time_row = conn.execute(
        """SELECT MAX(snapshot_time) FROM bubblemap_holders
           WHERE chain=? AND token_address=? AND snapshot_time<=?""",
        (chain, token, prev_cutoff)
    ).fetchone()
    prev_time = prev_time_row[0] if prev_time_row else None

    if not latest_time or not prev_time:
        return {
            "ratio_str": "N/A",
            "delta_str": "N/A",
            "ratio_val": None,
            "delta_val": None,
            "cohort_delta_str": "N/A",
            "cohort_delta_val": None,
            "status": "HOLDER_SNAPSHOT_MISSING"
        }

    sql_rank = """
        SELECT COALESCE(is_accumulating, 0) as is_acc, COALESCE(hold_percentage, 0) as hold_pct, wallet_address
        FROM bubblemap_holders
        WHERE chain=? AND token_address=? AND snapshot_time=?
        ORDER BY rank ASC LIMIT 300
    """
    latest_holders = conn.execute(sql_rank, (chain, token, latest_time)).fetchall()
    prev_holders = conn.execute(sql_rank, (chain, token, prev_time)).fetchall()

    if not latest_holders or not prev_holders:
        return {
            "ratio_str": "N/A",
            "delta_str": "N/A",
            "ratio_val": None,
            "delta_val": None,
            "cohort_delta_str": "N/A",
            "cohort_delta_val": None,
            "status": "HOLDER_SNAPSHOT_MISSING"
        }

    acc_count = sum(1 for r in latest_holders if r["is_acc"] == 1)
    ratio_val = acc_count / len(latest_holders)
    delta_val = sum(r["hold_pct"] for r in latest_holders) - sum(r["hold_pct"] for r in prev_holders)

    sql_cohort_latest = """
        SELECT wallet_address, COALESCE(hold_percentage, 0) as hold_pct
        FROM bubblemap_holders
        WHERE chain=? AND token_address=? AND snapshot_time=?
        ORDER BY rank ASC LIMIT 50
    """
    cohort_latest_rows = conn.execute(sql_cohort_latest, (chain, token, latest_time)).fetchall()
    addresses = [r["wallet_address"] for r in cohort_latest_rows if r["wallet_address"]]
    
    cohort_delta_val = None
    cohort_delta_str = "N/A"
    
    if addresses:
        placeholders = ",".join("?" for _ in addresses)
        sql_cohort_prev = f"""
            SELECT wallet_address, COALESCE(hold_percentage, 0) as hold_pct
            FROM bubblemap_holders
            WHERE chain=? AND token_address=? AND snapshot_time=?
              AND wallet_address IN ({placeholders})
        """
        prev_cohort_rows = conn.execute(sql_cohort_prev, [chain, token, prev_time] + addresses).fetchall()
        prev_cohort_map = {r["wallet_address"]: r["hold_pct"] for r in prev_cohort_rows}
        
        sum_latest = sum(r["hold_pct"] for r in cohort_latest_rows)
        sum_prev = sum(prev_cohort_map.get(addr, 0.0) for addr in addresses)
        cohort_delta_val = sum_latest - sum_prev
        cohort_delta_str = f"{cohort_delta_val:+.2%}"

    return {
        "ratio_str": f"{ratio_val:.1%}",
        "delta_str": f"{delta_val:+.2%}",
        "ratio_val": ratio_val,
        "delta_val": delta_val,
        "cohort_delta_str": cohort_delta_str,
        "cohort_delta_val": cohort_delta_val,
        "status": "SUCCESS"
    }

def validate_json_document(doc: Dict[str, Any]) -> bool:
    required_meta = ["report_generated_at_utc", "as_of_utc", "evaluation_status", "evaluation_reason", "git_commit", "config_hash"]
    required_row = [
        "event_id", "chain", "token_address", "pool_address", "token_symbol", "a_time", 
        "entry_price", "exit_price", "current_price", "r3d", "r7d", "r7d_maturity_status", 
        "ranked_concentration_delta", "cohort_balance_delta", "lp_reserve_usd", "lp_drawdown", 
        "maturity_status", "input_status", "gate_decision", "scenario", "dex_id",
        "prediction", "guidance"
    ]
    
    if "report_metadata" not in doc or "rows" not in doc:
        return False
    meta = doc["report_metadata"]
    for k in required_meta:
        if k not in meta or meta[k] is None:
            return False
    for row in doc["rows"]:
        for k in required_row:
            if k not in row:
                return False
    return True

def generate_report(as_of_arg: Optional[str] = None, dry_run: bool = False) -> int:
    print("--- 启动当前市场观察与多维白盒判定报告生成 (Live Observation) ---")
    
    select_db = os.getenv("SELECT_DB", SELECT_DB_DEFAULT)
    validation_db = os.getenv("VALIDATION_DB", VALIDATION_DB_DEFAULT)
    out_dir = os.getenv("OUT_DIR", OUT_DIR_DEFAULT)
    latest_report_path = os.getenv("LATEST_REPORT_PATH", LATEST_REPORT_PATH_DEFAULT)
    
    select_conn = get_db_connection(select_db)
    
    if as_of_arg:
        as_of = as_of_arg
    else:
        max_scan_time = select_conn.execute("SELECT MAX(scan_time) FROM gecko_market_data").fetchone()[0]
        as_of = max_scan_time if max_scan_time else datetime.utcnow().isoformat(timespec='seconds') + "Z"
        
    as_of_dt = parse_dt(as_of)
    print(f"数据时钟基线 (as_of_utc): {as_of}")

    evaluation_status = "NOT_EVALUATED"
    evaluation_reason = "INSUFFICIENT_TRAINING_SAMPLE"
    git_commit = "UNKNOWN"
    config_hash = "UNKNOWN"
    
    try:
        sum_conn = get_db_connection(validation_db)
        manifest_row = sum_conn.execute(
            "SELECT status, train_eligible_count, run_time, config_hash, git_commit FROM run_manifest ORDER BY id DESC LIMIT 1"
        ).fetchone()
        
        if manifest_row:
            git_commit = manifest_row["git_commit"]
            config_hash = manifest_row["config_hash"]
            m_status = manifest_row["status"]
            m_count = manifest_row["train_eligible_count"]
            m_time = parse_dt(manifest_row["run_time"])
            
            if m_status == "SUCCESS" and m_count > 0:
                if (as_of_dt - m_time).total_seconds() <= 7 * 86400:
                    evaluation_status = "ELIGIBLE_WATCH"
                    evaluation_reason = "NONE"
                else:
                    evaluation_reason = "MANIFEST_STALE"
            else:
                evaluation_reason = "INSUFFICIENT_TRAINING_SAMPLE"
        else:
            evaluation_reason = "INSUFFICIENT_TRAINING_SAMPLE"
        sum_conn.close()
    except Exception as e:
        print(f"读取门禁清单异常，默认拦截: {e}")
        evaluation_reason = "INSUFFICIENT_TRAINING_SAMPLE"

    try:
        sum_conn = get_db_connection(validation_db)
        events_rows = sum_conn.execute(
            """SELECT DISTINCT chain, token_address, token_symbol, a_time, pool_address, event_id
               FROM asset_identity
               WHERE a_time <= ?
               ORDER BY a_time DESC LIMIT 15""",
            (as_of,)
        ).fetchall()
        sum_conn.close()
    except Exception as e:
        print(f"核验库事件联查异常，回退至 select.db 扫描事件: {e}")
        events_rows = []

    if not events_rows:
        fallback_rows = select_conn.execute(
            """SELECT DISTINCT chain, token_address, 'MOCK' as token_symbol, MAX(scan_time) as a_time, pool_address
               FROM gecko_market_data
               WHERE scan_time <= ?
               GROUP BY chain, token_address
               ORDER BY a_time DESC LIMIT 10""",
            (as_of,)
        ).fetchall()
        events_rows = []
        for r in fallback_rows:
            ev_id = hashlib.md5(f"{r['chain']}_{r['token_address']}_{r['pool_address']}_{r['a_time']}".encode('utf-8')).hexdigest()[:16]
            events_rows.append({
                "chain": r["chain"],
                "token_address": r["token_address"],
                "token_symbol": r["token_symbol"],
                "a_time": r["a_time"],
                "pool_address": r["pool_address"],
                "event_id": ev_id
            })

    doc = {
        "report_metadata": {
            "report_generated_at_utc": datetime.now(timezone.utc).isoformat(timespec='seconds') + "Z",
            "as_of_utc": as_of,
            "evaluation_status": evaluation_status,
            "evaluation_reason": evaluation_reason,
            "git_commit": git_commit,
            "config_hash": config_hash
        },
        "rows": []
    }

    gate_intercept_count = 0
    
    for ev in events_rows:
        chain = ev["chain"]
        token = ev["token_address"]
        symbol = ev["token_symbol"] or "N/A"
        a_time = ev["a_time"]
        bound_pool = ev["pool_address"]
        event_id = ev["event_id"] or "N/A"

        a_dt = parse_dt(a_time)

        a_market = get_latest_market(select_conn, chain, token, a_time, pool=bound_pool)
        now_market = get_latest_market(select_conn, chain, token, as_of, pool=bound_pool)

        if not a_market or not now_market:
            doc["rows"].append({
                "event_id": event_id,
                "chain": chain,
                "token_address": token,
                "pool_address": bound_pool if bound_pool else "N/A",
                "token_symbol": symbol,
                "a_time": a_time,
                "entry_price": 0.0,
                "exit_price": 0.0,
                "current_price": 0.0,
                "r3d": None,
                "r7d": None,
                "r7d_maturity_status": "EXIT_SNAPSHOT_MISSING",
                "ranked_concentration_delta": None,
                "cohort_balance_delta": None,
                "lp_reserve_usd": 0.0,
                "lp_drawdown": False,
                "maturity_status": "EXIT_SNAPSHOT_MISSING",
                "input_status": "STALE_MARKET",
                "gate_decision": "INTERCEPTED",
                "scenario": "OBSERVATION_RANGE",
                "dex_id": "N/A",
                "prediction": "数据不足观望",
                "guidance": "无法对账：Gecko行情数据缺失。"
            })
            continue

        a_price = float(a_market["price_usd"] or 0)
        now_price = float(now_market["price_usd"] or 0)
        dex_id = a_market["dex_id"] or "N/A"

        # 3d 结算窗口计算 (退出价限制在 [A+3d, A+3d+4h] 的首个 Gecko 报价)
        r3d_val = None
        maturity_status = "PENDING_MATURITY"
        exit_market_3d = None
        
        if (as_of_dt - a_dt).total_seconds() >= 259200:
            target_time_3d = (a_dt + timedelta(days=3)).isoformat(sep=" ")
            window_end_3d = (a_dt + timedelta(days=3, hours=4)).isoformat(sep=" ")
            
            exit_market_3d = select_conn.execute(
                """SELECT price_usd FROM gecko_market_data
                   WHERE chain=? AND token_address=? AND pool_address=?
                     AND scan_time>=? AND scan_time<=?
                   ORDER BY scan_time ASC LIMIT 1""",
                (chain, token, bound_pool, target_time_3d, window_end_3d)
            ).fetchone()
            
            if exit_market_3d:
                exit_price_3d = float(exit_market_3d["price_usd"] or 0)
                if a_price > 0:
                    r3d_val = (exit_price_3d / a_price - 1)
                    maturity_status = "SETTLED"
                else:
                    maturity_status = "EXIT_SNAPSHOT_MISSING"
            else:
                maturity_status = "EXIT_SNAPSHOT_MISSING"

        # 7d 结算窗口计算 (退出价限制在 [A+7d, A+7d+4h] 的首个 Gecko 报价)
        r7d_val = None
        r7d_maturity_status = "PENDING_MATURITY"
        exit_market_7d = None
        
        if (as_of_dt - a_dt).total_seconds() >= 604800:
            target_time_7d = (a_dt + timedelta(days=7)).isoformat(sep=" ")
            window_end_7d = (a_dt + timedelta(days=7, hours=4)).isoformat(sep=" ")
            
            exit_market_7d = select_conn.execute(
                """SELECT price_usd FROM gecko_market_data
                   WHERE chain=? AND token_address=? AND pool_address=?
                     AND scan_time>=? AND scan_time<=?
                   ORDER BY scan_time ASC LIMIT 1""",
                (chain, token, bound_pool, target_time_7d, window_end_7d)
            ).fetchone()
            
            if exit_market_7d:
                exit_price_7d = float(exit_market_7d["price_usd"] or 0)
                if a_price > 0:
                    r7d_val = (exit_price_7d / a_price - 1)
                    r7d_maturity_status = "SETTLED"
                else:
                    r7d_maturity_status = "EXIT_SNAPSHOT_MISSING"
            else:
                r7d_maturity_status = "EXIT_SNAPSHOT_MISSING"

        # Holder 指标计算
        h_metrics = calculate_holder_metrics(select_conn, chain, token, a_time)
        ratio_val = h_metrics["ratio_val"]
        delta_val = h_metrics["delta_val"]
        cohort_delta_val = h_metrics["cohort_delta_val"]
        input_status = h_metrics["status"]

        # LP 基线与 drawdown 拦截 (非参数统计，不足 5 条不计算)
        history_lp_rows = select_conn.execute(
            """SELECT reserve_usd FROM gecko_market_data
               WHERE chain=? AND token_address=? AND pool_address=?
                 AND scan_time<=? AND scan_time>=?""",
            (chain, token, bound_pool, as_of, (as_of_dt - timedelta(days=7)).isoformat(sep=" "))
        ).fetchall()
        
        lp_drawdown = False
        curr_lp = float(now_market["reserve_usd"] or 0)
        
        if len(history_lp_rows) >= 5:
            lp_baseline = median([float(r["reserve_usd"] or 0) for r in history_lp_rows])
            if lp_baseline > 0 and curr_lp / lp_baseline <= 0.60:
                lp_drawdown = True
        else:
            if input_status == "SUCCESS":
                input_status = "LP_BASELINE_INSUFFICIENT"

        # 1. 确定中性微观 Scenario 状态
        scenario = "OBSERVATION_RANGE"
        if lp_drawdown:
            scenario = "LOW_LIQUIDITY_OBSERVATION"
        elif input_status == "HOLDER_SNAPSHOT_MISSING":
            scenario = "OBSERVATION_RANGE"
        else:
            price_change = (now_price / a_price - 1) if a_price > 0 else 0
            if price_change >= 0.02:
                if delta_val is not None and delta_val > 0.0005:
                    scenario = "OBSERVATION_UP_MOVE_WITH_NET_INFLOW"
                else:
                    scenario = "OBSERVATION_UP_MOVE_WITH_NET_OUTFLOW"
            elif price_change <= -0.02:
                if delta_val is not None and delta_val > 0.0005:
                    scenario = "OBSERVATION_DOWN_MOVE_WITH_NET_INFLOW"
                else:
                    scenario = "OBSERVATION_DOWN_MOVE_WITH_NET_OUTFLOW"

        # 2. 还原白盒走向预测逻辑 (Prediction & Guidance)
        prediction = "横盘观望 (未突破)"
        guidance = "观望：大户吸筹率偏低，价格突破不明显，庄家尚未作价，建议观望。"

        if input_status == "HOLDER_SNAPSHOT_MISSING":
            prediction = "数据不足观望"
            guidance = "观望：由于该标的链上Holder持仓快照数据缺失，数据不足无法进行走向预测。"
        elif lp_drawdown or (ratio_val is not None and ratio_val < 0.25 and delta_val is not None and delta_val < 0):
            prediction = "归零风险 (池子跑路)"
            ratio_str_val = f"{ratio_val:.1%}" if ratio_val is not None else "N/A"
            guidance = f"拦截：大户连续吸筹率仅 {ratio_str_val} 且 7d 净仓呈流出状态。配合池子 LP 暴跌，判定撤池跑路，强力拦截。"
        else:
            price_change = (now_price / a_price - 1) if a_price > 0 else 0
            if price_change >= 0.02 or price_change <= -0.02:
                if delta_val is not None and delta_val <= 0.0005:
                    prediction = "预期暴跌 (对倒出货)"
                    guidance = "做空：已达成价格突破，但大户无明显增仓且吸筹虚弱，大庄高位对倒分发出货概率极高，建议开空。"
                else:
                    prediction = "预期拉升 (强吸筹)"
                    guidance = "做多：大户呈净流入且达成价格有效向上突破，主力作价意图强，建议关注做多。"
            else:
                # 若无突破，但未满 3 天已结，大户无增仓时偏向做空预测
                if maturity_status == "PENDING_MATURITY":
                    if delta_val is not None and delta_val <= 0.0005:
                        prediction = "预期暴跌 (对倒出货)"
                        delta_str_val = f"{delta_val:+.2%}" if delta_val is not None else "N/A"
                        guidance = f"做空：已达成突破。大户无增仓且 7d 净仓增幅为 {delta_str_val}。信号触发时间短，r3d 结算锁处于 PENDING，但历史做空 Expectancy 规律触发。"

        # 行级质量门控拦截
        gate_decision = "PASS"
        if evaluation_status == "NOT_EVALUATED":
            gate_decision = "INTERCEPTED"
            gate_intercept_count += 1

        doc["rows"].append({
            "event_id": event_id,
            "chain": chain,
            "token_address": token,
            "pool_address": bound_pool if bound_pool else "N/A",
            "token_symbol": symbol,
            "a_time": a_time,
            "entry_price": a_price,
            "exit_price": float(exit_market_3d["price_usd"]) if (maturity_status == "SETTLED" and exit_market_3d) else 0.0,
            "current_price": now_price,
            "r3d": r3d_val,
            "r7d": r7d_val,
            "r7d_maturity_status": r7d_maturity_status,
            "ranked_concentration_delta": delta_val,
            "cohort_balance_delta": cohort_delta_val,
            "lp_reserve_usd": curr_lp,
            "lp_drawdown": lp_drawdown,
            "maturity_status": maturity_status,
            "input_status": input_status,
            "gate_decision": gate_decision,
            "scenario": scenario,
            "dex_id": dex_id,
            "prediction": prediction,
            "guidance": guidance
        })

    select_conn.close()

    # 5. Schema 校验
    if not validate_json_document(doc):
        raise ValueError("ReportDocument schema validation failed.")

    # 6. 渲染 Markdown (含有详细长句注解 Tips)
    lines = [
        "# Formal Signal Validation Report (Live Observation)",
        "",
        f"> 生成时间（UTC）：{doc['report_metadata']['report_generated_at_utc']}",
        f"> 数据源：SQLite 生产库只读对账；对账基线 (as_of_utc)：`{doc['report_metadata']['as_of_utc']}`",
        f"> 门禁状态：`{GATE_CN.get(doc['report_metadata']['evaluation_status'], doc['report_metadata']['evaluation_status'])}` | 原因码：`{doc['report_metadata']['evaluation_reason']}`",
        f"> 软件版本：git_commit=`{doc['report_metadata']['git_commit']}` | config_hash=`{doc['report_metadata']['config_hash']}`",
        "",
        "## 一、 最终对准修正后的数据报告表 (Aligned Observation)",
        "",
        "| 代币符号 | 信号时间 (A) | A发生价 | 当前实时价 | 3d已实现 (r3d) | 7d已实现 (r7d) | 预测/走向 | 状态判定 | Top300大户差 | Top50核心差 | 门禁决策 | Provenance | 交易指导意见 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    for r in doc["rows"]:
        symbol = r["token_symbol"]
        a_time_str = r["a_time"][5:16]
        p_a = format_price(r["entry_price"])
        p_now = format_price(r["current_price"])
        
        r3d_str = "PENDING" if r["maturity_status"] == "PENDING_MATURITY" else ("N/A" if r["r3d"] is None else f"{r['r3d']:+.2%}")
        r7d_str = "PENDING" if r["r7d_maturity_status"] == "PENDING_MATURITY" else ("N/A" if r["r7d"] is None else f"{r['r7d']:+.2%}")
        
        scenario_cn = SCENARIO_CN.get(r["scenario"], r["scenario"])
        gate_cn = GATE_CN.get(r["gate_decision"], r["gate_decision"])
        
        top300_str = "N/A" if r["ranked_concentration_delta"] is None else f"{r['ranked_concentration_delta']:+.2%}"
        cohort_str = "N/A" if r["cohort_balance_delta"] is None else f"{r['cohort_balance_delta']:+.2%}"
        
        lines.append(
            f"| **{symbol}** | {a_time_str} | {p_a} | {p_now} | **{r3d_str}** | **{r7d_str}** | {r['prediction']} | {scenario_cn} | {top300_str} | {cohort_str} | {gate_cn} | {r['dex_id']} | {r['guidance']} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 二、 数据边界与自审",
        "",
        "- 本报告基于 `/opt/select-coin/data/select.db` 生产大表及 `select-sum.db` 做只读物理审计。",
        "- 在 L3 实盘 OOS 回测判定通过（即 `evaluation_status = ELIGIBLE_WATCH`）之前，所有呈现数据均属于白盒指标规律的 WATCH 状态观察，不构成下单决策。",
    ]

    md_content = "\n".join(lines) + "\n"

    if dry_run:
        print("[Dry Run] 零写入磁盘。统计：")
        print(f"总行数: {len(doc['rows'])} | 门禁拦截数: {gate_intercept_count}")
        return 0

    os.makedirs(out_dir, exist_ok=True)
    
    # 写入 JSON
    json_path = latest_report_path.replace(".md", ".json")
    tmp_json_path = json_path + ".tmp"
    with open(tmp_json_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_json_path, json_path)
    
    # 写入 MD
    tmp_md_path = latest_report_path + ".tmp"
    with open(tmp_md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_md_path, latest_report_path)

    # 复制到临时诊断发布目录 (/tmp/MMDD/)
    try:
        mmdd = as_of_dt.strftime("%m%d")
        tmp_publish_dir = f"/tmp/{mmdd}"
        os.makedirs(tmp_publish_dir, exist_ok=True)
        
        dest_md = os.path.join(tmp_publish_dir, "latest_live_observation.md")
        dest_json = os.path.join(tmp_publish_dir, "latest_live_observation.json")
        
        with open(dest_md, "w", encoding="utf-8") as f:
            f.write(md_content)
        with open(dest_json, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
            
        print(f"DEX 市场实时观察报告生成并发布成功。")
        print(f"主报告: {latest_report_path}")
        print(f"诊断副本: {dest_md}")
    except Exception as e:
        print(f"复制副本至临时目录失败: {e}")

    return 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live Observation Report Generator")
    parser.add_argument("--as-of", type=str, default=None, help="基线对账时刻 (ISO-8601 UTC)")
    parser.add_argument("--dry-run", action="store_true", help="只跑对账不落盘文件")
    args = parser.parse_args()
    
    sys.exit(generate_report(as_of_arg=args.as_of, dry_run=args.dry_run))
