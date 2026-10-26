"""
全网 CEX 资金流时序加速度与微观筹码背离分析引擎 (Screen 9 Engine)
基于 select.db: cex_flow_snapshots 连续时序计算：
1. 流向一阶导数加速度 (Flow Delta & Delta %)
2. 筹码微观结构 (笔数变化率 & 单笔均额 Ticket Size)
3. 3日流向一致性 (Directional Consistency)
4. DEX-CEX 跨域时空多空背离指数 (Divergence Index)
"""
from __future__ import annotations
from datetime import datetime
import os
import sqlite3
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger("meta-verdict")

SRC_DB_PATH = "/opt/select-coin/data/select.db"

def get_cex_flow_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(SRC_DB_PATH, timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 60000;")
    return conn

def analyze_cex_flow_divergence(all_arbitrated: Optional[List[Any]] = None) -> Dict[str, Any]:
    """分析最新 CEX 资金流时序加速度与多空背离"""
    conn = get_cex_flow_conn()
    try:
        # 获取最新的批次时间列表
        batches = [r[0] for r in conn.execute(
            "SELECT DISTINCT batch_date FROM cex_flow_snapshots ORDER BY batch_date DESC LIMIT 9"
        ).fetchall()]
        
        if len(batches) < 2:
            logger.warning("CEX 资金流批次数据不足 2 批，无法计算时序加速度")
            return {"top_alerts": [], "micro_matrix": [], "stats": {"total_tokens": 0, "coverage_pct": 0.0}}

        latest_batch = batches[0]
        prev_batch = batches[1]

        # ── 新鲜度门禁 (Watchdog Gate): 超过 24h 视为数据源停滞，阻断陈旧时序差分 ──
        batch_dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
            try:
                batch_dt = datetime.strptime(latest_batch.split(".")[0].replace("Z", ""), fmt)
                break
            except Exception:
                continue

        now = datetime.now()
        max_hours = 24.0
        if batch_dt and (now - batch_dt).total_seconds() / 3600.0 > max_hours:
            diff_h = (now - batch_dt).total_seconds() / 3600.0
            logger.warning(
                "第九屏检测到 CEX 快照已过期 (批次: %s, 距今 %.1fh > %.1fh)，触发静默熔断阻断幽灵输出",
                latest_batch, diff_h, max_hours
            )
            return {
                "top_alerts": [],
                "micro_matrix": [],
                "stats": {
                    "total_tokens": 0,
                    "coverage_pct": 0.0,
                    "status": "SOURCE_STALE",
                    "latest_batch": latest_batch,
                    "stale_hours": round(diff_h, 1),
                    "message": f"数据源过期熔断 (快照: {latest_batch}, 距今 {diff_h:.1f}h)"
                }
            }

        # 读取最新批次的全部代币
        latest_rows = conn.execute("""
            SELECT item_name, inflow_usd, outflow_usd, net_flow_usd, tx_count, ticket_avg_usd
            FROM cex_flow_snapshots
            WHERE batch_date = ? AND category = 'TOKEN'
        """, (latest_batch,)).fetchall()

        token_map_latest = {r["item_name"].upper(): dict(r) for r in latest_rows}

        # 读取前一期批次代币
        prev_rows = conn.execute("""
            SELECT item_name, inflow_usd, outflow_usd, net_flow_usd, tx_count, ticket_avg_usd
            FROM cex_flow_snapshots
            WHERE batch_date = ? AND category = 'TOKEN'
        """, (prev_batch,)).fetchall()

        token_map_prev = {r["item_name"].upper(): dict(r) for r in prev_rows}

        # 读取所有 9 期以计算 3d 一致性
        all_hist_rows = conn.execute("""
            SELECT item_name, batch_date, net_flow_usd
            FROM cex_flow_snapshots
            WHERE category = 'TOKEN'
            ORDER BY batch_date ASC
        """).fetchall()

        hist_by_token: Dict[str, List[float]] = {}
        for r in all_hist_rows:
            sym = r["item_name"].upper()
            hist_by_token.setdefault(sym, []).append(r["net_flow_usd"])

        # 匹配 AI-SUM 标的
        aisum_symbols = set()
        arbitrated_map = {}
        if all_arbitrated:
            for a in all_arbitrated:
                sym = getattr(a, "token_symbol", "").upper()
                if sym:
                    aisum_symbols.add(sym)
                    arbitrated_map[sym] = a

        records = []
        for sym, cur in token_map_latest.items():
            prev = token_map_prev.get(sym)
            if not prev:
                continue

            net_latest = cur["net_flow_usd"]
            net_prev = prev["net_flow_usd"]
            tx_latest = cur["tx_count"] or 0
            tx_prev = prev["tx_count"] or 0
            ticket_latest = cur["ticket_avg_usd"] or 0.0
            vol_latest = cur["inflow_usd"] + cur["outflow_usd"]

            # 1. Flow Delta & Delta %
            flow_delta = net_latest - net_prev
            denom_flow = max(abs(net_prev), 10000.0)
            flow_delta_pct = (flow_delta / denom_flow) * 100.0

            # 2. Tx Delta %
            tx_delta = tx_latest - tx_prev
            denom_tx = max(tx_prev, 5)
            tx_delta_pct = (tx_delta / denom_tx) * 100.0

            # 3. 3-day Consistency
            token_all_net = hist_by_token.get(sym, [net_latest])
            outflow_batches = sum(1 for f in token_all_net if f < 0)
            consistency_pct = (outflow_batches / len(token_all_net)) * 100.0

            # 4. Crowd Type Archetype
            if vol_latest >= 20000:
                if ticket_latest >= 25000 and tx_latest <= 45:
                    crowd_type = "🐳 巨鲸定点"
                elif tx_latest >= 80 and ticket_latest <= 3000:
                    crowd_type = "👥 散户蜂拥"
                elif tx_latest < 10 and vol_latest < 15000:
                    crowd_type = "💀 流动性枯竭"
                else:
                    crowd_type = "⚖️ 均衡换手"
            else:
                crowd_type = "💤 微量平稳"

            # 5. Alert Trigger & Qualification
            dex_score = getattr(arbitrated_map.get(sym), "meta_score", 0.0) if sym in arbitrated_map else None

            alert = "正常"
            warning_msg = "资金流动平稳"
            if flow_delta >= 100000 or (flow_delta_pct >= 50.0 and net_latest > 30000):
                alert = "🚨 突发充值激增"
                if dex_score is not None and dex_score >= 7.0:
                    warning_msg = "⚠️ 诱多出货背离！DEX强吸筹但CEX突发巨额充值流入，高危预警"
                else:
                    warning_msg = "🛑 突发大额充值流入，警惕庄家借机高位砸盘出货"
            elif flow_delta <= -100000 or (flow_delta_pct <= -50.0 and net_latest < -30000):
                alert = "🚀 提现大幅提速"
                warning_msg = "🚀 提现大幅提速离场，主力真金锁仓动能持续增强"
            elif abs(flow_delta_pct) >= 20.0 and vol_latest >= 50000:
                alert = "⚡ 流向阶跃异动"
                warning_msg = "⚡ 资金流向发生大幅阶跃漂移(±20%+)，注意趋势逆转"

            is_aisum = sym in aisum_symbols
            records.append({
                "token": sym,
                "is_aisum": is_aisum,
                "net_latest": net_latest,
                "flow_delta": flow_delta,
                "flow_delta_pct": flow_delta_pct,
                "tx_latest": tx_latest,
                "tx_delta_pct": tx_delta_pct,
                "ticket_latest": ticket_latest,
                "vol_latest": vol_latest,
                "consistency_pct": consistency_pct,
                "crowd_type": crowd_type,
                "alert": alert,
                "warning_msg": warning_msg,
                "dex_score": dex_score,
            })

        # 优先筛选 AI-SUM 标的，若不够再补充高额全网异动标的
        aisum_records = [r for r in records if r["is_aisum"]]
        other_records = [r for r in records if not r["is_aisum"] and abs(r["flow_delta"]) >= 50000]

        top_aisum = sorted(aisum_records, key=lambda x: abs(x["flow_delta"]), reverse=True)
        top_other = sorted(other_records, key=lambda x: abs(x["flow_delta"]), reverse=True)

        top_alerts = (top_aisum[:15] + top_other[:5])[:15]

        # 构建微观矩阵代表标的
        whales = [r["token"] for r in records if r["crowd_type"] == "🐳 巨鲸定点"][:5]
        retail = [r["token"] for r in records if r["crowd_type"] == "👥 散户蜂拥"][:5]
        illiquid = [r["token"] for r in records if r["crowd_type"] == "💀 流动性枯竭"][:5]

        micro_matrix = [
            {
                "category": "🐳 巨鲸定点搬砖",
                "tokens": ", ".join(f"**{t}**" for t in whales) if whales else "—",
                "tx_range": "10 ~ 45 笔",
                "ticket_range": "≥ $25,000",
                "desc": "机构/主力定点大额建仓或出货，单笔权重极高",
                "strategy": "跟随主力资金流向，防范巨鲸反向收割",
            },
            {
                "category": "👥 散户群体蜂拥",
                "tokens": ", ".join(f"**{t}**" for t in retail) if retail else "—",
                "tx_range": "≥ 80 笔",
                "ticket_range": "≤ $3,000",
                "desc": "笔数剧增但单笔微小，散户盲目追高或恐慌抛售",
                "strategy": "逆向操作，警惕羊群效应踩踏破位",
            },
            {
                "category": "💀 流动性断崖枯竭",
                "tokens": ", ".join(f"**{t}**" for t in illiquid) if illiquid else "—",
                "tx_range": "< 10 笔",
                "ticket_range": "< $500",
                "desc": "CEX 侧交易活跃度接近归零，缺乏外部承接盘",
                "strategy": "仅限 DEX 极浅池博弈，严禁大资金介入",
            },
        ]

        cov_count = len(aisum_records)
        total_aisum = len(aisum_symbols) if aisum_symbols else 210

        return {
            "top_alerts": top_alerts,
            "micro_matrix": micro_matrix,
            "stats": {
                "latest_batch": latest_batch,
                "prev_batch": prev_batch,
                "total_tokens": len(records),
                "aisum_covered": cov_count,
                "coverage_pct": (cov_count / total_aisum) * 100.0 if total_aisum > 0 else 0.0,
            }
        }
    finally:
        conn.close()

if __name__ == "__main__":
    res = analyze_cex_flow_divergence()
    print("Stats:", res["stats"])
    print("Top alerts count:", len(res["top_alerts"]))
    for a in res["top_alerts"][:5]:
        print(a["token"], a["net_latest"], a["flow_delta_pct"], a["crowd_type"], a["alert"])
