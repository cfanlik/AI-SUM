"""
DefiLlama 外挂共振分析器 — 作为 meta-verdict 后置勾载程序，计算 APY/TVL 及 Fees/Revenue 修正分并对 pump_readiness 进行外挂式补偿
"""
import sqlite3
import os
import logging
from datetime import datetime

logger = logging.getLogger("defillama_resonator")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# 路径定义
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUM_DB_PATH = os.path.join(PROJECT_ROOT, "select-sum.db")
# 关联 select-coin 数据库
SRC_DB_PATH = os.path.join(os.path.dirname(PROJECT_ROOT), "select-coin", "data", "select.db")

def get_connection(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout = 60000;")
    return conn

def calculate_defillama_bias(metrics: dict, gecko_data: dict) -> tuple[float, list[str]]:
    """计算 DefiLlama 外挂修正分 ΔS"""
    delta_s = 0.0
    badges = []
    
    tvl = float(metrics.get("tvl", 0) or 0)
    apy = float(metrics.get("apy", 0) or 0)
    fees = float(metrics.get("daily_fees", 0) or 0)
    
    fdv = float(gecko_data.get("fdv_usd", 0) or 0)
    mcap = float(gecko_data.get("market_cap_usd", 0) or 0)
    effective_cap = mcap if mcap > 0 else fdv
    
    # D12.1 APY 质押与 TVL 偏离度 (Max 5分)
    if tvl >= 500000:
        delta_s += 5.0
        badges.append("🤫大额锁仓")
    elif tvl >= 100000:
        delta_s += 4.0
        if apy >= 5.0:
            badges.append("🤫高息吸筹")
        else:
            badges.append("🤫中额锁仓")
    elif tvl >= 10000:
        delta_s += 3.0
        if apy >= 5.0:
            badges.append("🤫高息吸筹")
            
    # D12.2 协议手续费收益率 (Max 5分)
    if effective_cap > 0:
        fee_yield = fees / effective_cap
        if fee_yield >= 0.0005:  # 高造血比 (年化收益超 18%)
            delta_s += 5.0
            badges.append("🚀基本面突破")
        elif fee_yield >= 0.0001:  # 中等造血比
            delta_s += 3.0
            badges.append("🔍潜伏活跃")
            
    return delta_s, badges

def run_resonator(scan_time: str = None):
    if not os.path.exists(SUM_DB_PATH) or not os.path.exists(SRC_DB_PATH):
        logger.warning(f"Database file missing. SumDB: {SUM_DB_PATH} | SrcDB: {SRC_DB_PATH}")
        return
        
    conn_sum = get_connection(SUM_DB_PATH)
    conn_src = get_connection(SRC_DB_PATH)
    
    # 确定要扫描的 scan_time（默认最新一轮）
    if not scan_time:
        row = conn_sum.execute("SELECT MAX(scan_time) FROM meta_snapshots").fetchone()
        if not row or not row[0]:
            logger.warning("No snapshots found in meta_snapshots.")
            conn_sum.close()
            conn_src.close()
            return
        scan_time = row[0]
        
    logger.info(f"Running DefiLlama Resonator for scan_time: {scan_time}")
    
    # 1. 查出最新一轮的有评分代币
    meta_rows = conn_sum.execute(
        "SELECT * FROM meta_snapshots WHERE scan_time = ?", [scan_time]
    ).fetchall()
    
    updated_cnt = 0
    
    for meta in meta_rows:
        symbol = meta["token_symbol"]
        
        # ─── 幂等还原逻辑 ───
        # 1. 基于分项数据直接计算还原纯净原始评分
        master_score = float(meta["master_score"] or 0)
        opus_score = float(meta["opus_score"] or 0)
        unified_score = float(meta["unified_score"] or 0)
        whale_score = float(meta["whale_score"] or 0)
        cb_score = float(meta["cb_score"] or 0)
        hop2_score = float(meta["hop2_score"] or 0)
        
        original_score = round(master_score + opus_score + unified_score + whale_score + cb_score + hop2_score, 2)
        
        # 2. 推断纯净原始 verdict
        if original_score >= 3.0:
            original_verdict = "ACC"
        elif original_score <= -2.0:
            original_verdict = "DIST"
        else:
            original_verdict = "NEUTRAL"
            
        # 3. 推断纯净原始 stage
        master_signal = meta["master_signal"]
        whale_level = meta["whale_level"]
        cb_verdict = meta["cb_verdict"]
        
        if master_signal == "DIAMOND" and whale_level == "HIGH":
            original_stage = "CONTROLLED"
        elif cb_verdict in ("DEATH_SPIRAL", "LIQUIDITY_CRISIS"):
            original_stage = "DISTRIBUTING"
        elif original_verdict == "DIST":
            original_stage = "DISTRIBUTING"
        elif original_verdict == "ACC":
            if master_signal == "DIAMOND":
                original_stage = "CONTROLLED"
            else:
                original_stage = "ACCUMULATING"
        elif master_signal in ("RED", "YELLOW"):
            original_stage = "WATCHLIST"
        else:
            original_stage = "NEUTRAL"
        # ────────────────────
        
        # 查找代币地址
        addr_row = conn_src.execute(
            "SELECT token_address, chain FROM token_names WHERE symbol = ? LIMIT 1", [symbol]
        ).fetchone()
        
        if not addr_row:
            continue
            
        token_address = addr_row["token_address"]
        chain = addr_row["chain"]
        
        # 2. 读取 DefiLlama 最新指标
        metrics_row = conn_src.execute(
            """SELECT * FROM defillama_metrics 
               WHERE chain = ? AND contract_address = ? 
               ORDER BY scan_time DESC LIMIT 1""",
            [chain, token_address.lower()]
        ).fetchone()
        
        # 3. 读取 Gecko 最新指标
        gecko_row = conn_src.execute(
            """SELECT * FROM gecko_market_data 
               WHERE chain = ? AND token_address = ? 
               ORDER BY scan_time DESC LIMIT 1""",
            [chain, token_address.lower()]
        ).fetchone()
        
        if not metrics_row or not gecko_row:
            # 还原为纯净原始分以防脏数据
            conn_sum.execute(
                """UPDATE meta_snapshots 
                   SET meta_score = ?, meta_verdict = ?, stage = ?
                   WHERE token_symbol = ? AND scan_time = ?""",
                (original_score, original_verdict, original_stage, symbol, scan_time)
            )
            continue
            
        # 4. 计算外挂偏差修正分
        delta_s, badges = calculate_defillama_bias(dict(metrics_row), dict(gecko_row))
        
        new_score = original_score + delta_s
        new_verdict = original_verdict
        if new_score >= 3.0:
            new_verdict = "ACC"
            
        stage = original_stage
        if badges:
            badge_str = "/".join(badges)
            stage = f"{stage}({badge_str})"
            
        # 5. 更新数据库元快照（点对点原子写入）
        conn_sum.execute(
            """UPDATE meta_snapshots 
               SET meta_score = ?, meta_verdict = ?, stage = ?
               WHERE token_symbol = ? AND scan_time = ?""",
            (new_score, new_verdict, stage, symbol, scan_time)
        )
        if delta_s > 0:
            updated_cnt += 1
            logger.info(f"[Resonance Updated] {symbol} | Meta Score: {original_score} -> {new_score} | Badges: {badges}")
            
    conn_sum.commit()
    conn_sum.close()
    conn_src.close()
    
    logger.info(f"DefiLlama Resonator completed. {updated_cnt} tokens updated.")

if __name__ == "__main__":
    run_resonator()
