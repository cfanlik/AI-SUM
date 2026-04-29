"""
cost-basis-scan 成本重心漂移追踪 + watchlist 状态跃迁
"""
from __future__ import annotations
import sqlite3
import logging
from dataclasses import dataclass
from typing import Optional
from db_loader import CostHolder
import config

logger = logging.getLogger(__name__)


@dataclass
class GravityResult:
    """成本重心漂移结果"""
    current_gravity: float = 0.0
    last_gravity: float = 0.0
    current_price: float = 0.0
    last_price: float = 0.0
    drift_ratio: float = 0.0       # 重心变化率
    drift_label: str = ""           # LOCK_PUMP / CHURN_OUT / DIAMOND_HAND / STABLE


@dataclass
class WatchlistTransition:
    """watchlist 状态跃迁结果"""
    has_history: bool = False
    past_signal_level: str = ""
    past_trigger: str = ""
    transition_verdict: str = ""    # DEATH_SPIRAL / SQUEEZE_ACC / ""


def compute_gravity(holders: list[CostHolder]) -> float:
    """计算持仓加权成本重心"""
    total_weight = sum(h.hold_percentage for h in holders if h.hold_percentage > 0)
    if total_weight == 0:
        return 0
    return sum(
        h.hold_percentage * h.gmgn_avg_price
        for h in holders
    ) / total_weight


def detect_gravity_drift(
    conn: sqlite3.Connection,
    chain: str,
    token_address: str,
    current_gravity: float,
    current_price: float,
) -> GravityResult:
    """
    对比上次扫描结果，检测成本重心漂移方向。

    漂移判定:
      price↑ + gravity 不变 → LOCK_PUMP (锁仓拉升)
      price 横盘 + gravity↑ → CHURN_OUT (换手出货)
      price↓ + gravity 不变 → DIAMOND_HAND (钻石手)
    """
    result = GravityResult(
        current_gravity=current_gravity,
        current_price=current_price,
    )

    # 加载上次扫描结果
    last_row = conn.execute("""
        SELECT cost_gravity, gecko_price
        FROM cost_basis_snapshots
        WHERE chain = ? AND token_address = ?
        ORDER BY scan_time DESC
        LIMIT 1
    """, (chain, token_address)).fetchone()

    if not last_row or not last_row["cost_gravity"]:
        result.drift_label = "FIRST_SCAN"
        return result

    result.last_gravity = last_row["cost_gravity"]
    result.last_price = last_row["gecko_price"]

    if result.last_gravity <= 0 or result.last_price <= 0:
        result.drift_label = "NO_DATA"
        return result

    # 计算漂移
    price_delta = (current_price - result.last_price) / result.last_price
    gravity_delta = (current_gravity - result.last_gravity) / result.last_gravity
    result.drift_ratio = gravity_delta

    # 判定漂移方向
    gravity_stable = abs(gravity_delta) < config.GRAVITY_DRIFT_SIGNIFICANT
    price_up = price_delta > 0.05
    price_down = price_delta < -0.05
    price_flat = not price_up and not price_down
    gravity_up = gravity_delta > config.GRAVITY_DRIFT_SIGNIFICANT

    if price_up and gravity_stable:
        result.drift_label = "LOCK_PUMP"       # 锁仓拉升
    elif price_flat and gravity_up:
        result.drift_label = "CHURN_OUT"       # 换手出货
    elif price_down and gravity_stable:
        result.drift_label = "DIAMOND_HAND"    # 钻石手
    else:
        result.drift_label = "STABLE"

    return result


def check_watchlist_transition(
    conn: sqlite3.Connection,
    chain: str,
    token_address: str,
    c1_triggered: bool,
    c4_triggered: bool,
) -> WatchlistTransition:
    """
    检查 watchlist 历史状态与当前成本信号的交叉判定。

    规则:
      历史 DIAMOND/RED + C4(暴利出逃) → DEATH_SPIRAL
      历史 RED + C1(水下加仓) → SQUEEZE_ACC
    """
    result = WatchlistTransition()

    row = conn.execute("""
        SELECT signal_level, trigger_pattern, status
        FROM watchlist
        WHERE chain = ? AND token_address = ?
        LIMIT 1
    """, (chain, token_address)).fetchone()

    if not row:
        return result

    result.has_history = True
    result.past_signal_level = row["signal_level"] or ""
    result.past_trigger = row["trigger_pattern"] or ""

    level = result.past_signal_level.upper()

    # 历史 DIAMOND/EXTREME/CRITICAL + 当前暴利出逃
    if level in ("DIAMOND", "EXTREME", "CRITICAL", "RED") and c4_triggered:
        result.transition_verdict = "DEATH_SPIRAL"
        logger.info(f"watchlist 跃迁: {chain}/{token_address} "
                     f"历史={level} + C4 暴利出逃 → DEATH_SPIRAL")

    # 历史 RED + 当前水下加仓
    elif level == "RED" and c1_triggered:
        result.transition_verdict = "SQUEEZE_ACC"
        logger.info(f"watchlist 跃迁: {chain}/{token_address} "
                     f"历史=RED + C1 水下加仓 → SQUEEZE_ACC")

    return result
