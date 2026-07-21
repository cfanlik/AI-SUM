"""
突发拉伸 (Impulse Surge) 独立分析模块 (4阶 PnL 动量矩阵全量版)
基于 60 天最长保留周期，计算 S_7d, S_15d, S_30d, S_60d 4 阶 PnL 斜率，识别真突发拉伸并拦截死猫跳反弹
"""
import sqlite3, os, logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

@dataclass
class ImpulseSurgeResult:
    token_address: str
    token_symbol: str
    chain: str
    reserve_now: float
    reserve_14d_avg: float
    liq_ratio: float
    pnl_now: float
    slope_7d: float
    slope_15d: float
    slope_30d: float
    slope_60d: float
    whale_net_7d: int
    vol_ratio: float
    oscillation_cnt: int
    pattern: str  # ACCELERATING_SURGE / STABLE_HIGH_SURGE / DEAD_CAT_BOUNCE / GENERAL_SURGE
    is_triggered: bool
    trigger_reasons: List[str]

class ImpulseSurgeAnalyzer:
    def __init__(self, db_path: str = "/opt/AI-SUM/select-sum.db"):
        self.db_path = db_path

    def analyze(self) -> List[ImpulseSurgeResult]:
        if not os.path.exists(self.db_path):
            logger.error(f"Database not found: {self.db_path}")
            return []

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # 1. 提取全库所有代币
        cur.execute("SELECT DISTINCT token_address, token_symbol, chain FROM token_history WHERE computed_date >= date('now', '-60 days')")
        tokens = cur.fetchall()

        results = []
        for t in tokens:
            t_addr = t['token_address'].lower()
            t_sym = t['token_symbol'] or "UNKNOWN"
            t_chain = t['chain'] or "bsc"

            reasons = []

            # --- S1: 流动性结构突变 ---
            cur.execute("""
                WITH ranked AS (
                    SELECT reserve_usd,
                           ROW_NUMBER() OVER (ORDER BY computed_date DESC) as rn
                    FROM token_history
                    WHERE LOWER(token_address) = ? AND reserve_usd > 0 AND reserve_usd IS NOT NULL
                )
                SELECT 
                    (SELECT reserve_usd FROM ranked WHERE rn = 1) as r_now,
                    (SELECT AVG(reserve_usd) FROM ranked WHERE rn BETWEEN 2 AND 15) as r_avg
            """, (t_addr,))
            r_row = cur.fetchone()
            r_now = r_row['r_now'] if r_row and r_row['r_now'] else 0.0
            r_avg = r_row['r_avg'] if r_row and r_row['r_avg'] else 0.0
            liq_ratio = (r_now / r_avg) if r_avg > 0 else 1.0
            s1_hit = (r_avg > 0 and liq_ratio < 0.25)
            if s1_hit:
                reasons.append(f"S1: 流动性突降 (Ratio={liq_ratio:.2f})")

            # --- 4 阶 PnL 斜率矩阵计算 (7d, 15d, 30d, 60d) ---
            cur.execute("""
                WITH ranked AS (
                    SELECT pnl_ratio, computed_date,
                           ROW_NUMBER() OVER (ORDER BY computed_date DESC) as rn
                    FROM token_history
                    WHERE LOWER(token_address) = ? AND pnl_ratio IS NOT NULL
                )
                SELECT 
                    (SELECT pnl_ratio FROM ranked WHERE rn = 1) as pnl_now,
                    (SELECT pnl_ratio FROM ranked WHERE rn = 8) as pnl_7d,
                    (SELECT pnl_ratio FROM ranked WHERE rn = 16) as pnl_15d,
                    (SELECT pnl_ratio FROM ranked WHERE rn = 31) as pnl_30d,
                    (SELECT pnl_ratio FROM ranked WHERE rn = 61) as pnl_60d
            """, (t_addr,))
            pnl_row = cur.fetchone()
            pnl_now = pnl_row['pnl_now'] if pnl_row and pnl_row['pnl_now'] is not None else 0.0
            pnl_7d = pnl_row['pnl_7d'] if pnl_row and pnl_row['pnl_7d'] is not None else 0.0
            pnl_15d = pnl_row['pnl_15d'] if pnl_row and pnl_row['pnl_15d'] is not None else pnl_7d
            pnl_30d = pnl_row['pnl_30d'] if pnl_row and pnl_row['pnl_30d'] is not None else pnl_15d
            pnl_60d = pnl_row['pnl_60d'] if pnl_row and pnl_row['pnl_60d'] is not None else pnl_30d

            slope_7d = pnl_now - pnl_7d
            slope_15d = pnl_now - pnl_15d
            slope_30d = pnl_now - pnl_30d
            slope_60d = pnl_now - pnl_60d

            s2_hit = (slope_7d > 50.0)
            if s2_hit:
                reasons.append(f"S2: 4阶PnL动量 (S7d={slope_7d:.1f}, S15d={slope_15d:.1f})")

            # --- S3: 巨鲸净流入 ---
            cur.execute("""
                SELECT SUM(whale_entered - whale_exited) as net_in
                FROM token_history
                WHERE LOWER(token_address) = ? AND computed_date >= date('now', '-7 days')
            """, (t_addr,))
            w_row = cur.fetchone()
            whale_net = w_row['net_in'] if w_row and w_row['net_in'] else 0
            s3_hit = (whale_net > 5)
            if s3_hit:
                reasons.append(f"S3: 巨鲸净流入 (Net={whale_net})")

            # --- S4: 成交量脉冲 ---
            cur.execute("""
                WITH ranked AS (
                    SELECT volume_24h,
                           ROW_NUMBER() OVER (ORDER BY computed_date DESC) as rn
                    FROM token_history
                    WHERE LOWER(token_address) = ? AND volume_24h IS NOT NULL AND volume_24h > 0
                )
                SELECT 
                    (SELECT AVG(volume_24h) FROM ranked WHERE rn <= 3) as v3d,
                    (SELECT AVG(volume_24h) FROM ranked WHERE rn BETWEEN 4 AND 14) as v14d
            """, (t_addr,))
            v_row = cur.fetchone()
            v3d = v_row['v3d'] if v_row and v_row['v3d'] else 0.0
            v14d = v_row['v14d'] if v_row and v_row['v14d'] else 0.0
            vol_ratio = (v3d / v14d) if v14d > 0 else 0.0
            s4_hit = (v14d > 0 and vol_ratio > 2.0)
            if s4_hit:
                reasons.append(f"S4: 成交量脉冲 (Ratio={vol_ratio:.2f})")

            # --- S5: Verdict 振荡次数 ---
            cur.execute("""
                SELECT meta_verdict
                FROM meta_snapshots
                WHERE LOWER(token_address) = ? AND scan_time >= datetime('now', '-3 days')
                ORDER BY scan_time ASC
            """, (t_addr,))
            v_rows = [r['meta_verdict'] for r in cur.fetchall()]
            osc_cnt = 0
            for i in range(1, len(v_rows)):
                if v_rows[i] != v_rows[i-1]:
                    osc_cnt += 1
            s5_hit = (osc_cnt >= 3)
            if s5_hit:
                reasons.append(f"S5: 判定振荡抑制 (Flip={osc_cnt}次)")

            # 形态分类判定 (判定死猫跳与强主升浪)
            pattern = "GENERAL_SURGE"
            is_dead_cat = (slope_7d > 30.0 and (slope_30d < 0 or slope_60d < -100.0) and pnl_now < 0)
            
            if is_dead_cat:
                pattern = "DEAD_CAT_BOUNCE"
                is_triggered = False # 坑底死猫跳反弹强拦截，不打入突发拉伸强告警
            else:
                if slope_7d > 50.0 and slope_15d > 30.0 and slope_30d > 0:
                    if slope_7d > slope_15d and slope_15d > slope_30d:
                        pattern = "ACCELERATING_SURGE" # 凹向加速主升浪
                    else:
                        pattern = "STABLE_HIGH_SURGE"  # 高位稳态拉升
                
                is_triggered = (s1_hit and s2_hit) or (s2_hit and s3_hit and s4_hit) or s5_hit

            if is_triggered:
                results.append(ImpulseSurgeResult(
                    token_address=t_addr,
                    token_symbol=t_sym,
                    chain=t_chain,
                    reserve_now=r_now,
                    reserve_14d_avg=r_avg,
                    liq_ratio=liq_ratio,
                    pnl_now=pnl_now,
                    slope_7d=slope_7d,
                    slope_15d=slope_15d,
                    slope_30d=slope_30d,
                    slope_60d=slope_60d,
                    whale_net_7d=whale_net,
                    vol_ratio=vol_ratio,
                    oscillation_cnt=osc_cnt,
                    pattern=pattern,
                    is_triggered=is_triggered,
                    trigger_reasons=reasons
                ))

        conn.close()
        # 按 (触发条件数, 7d斜率) 降序排序
        results.sort(key=lambda x: (len(x.trigger_reasons), x.slope_7d), reverse=True)
        return results

