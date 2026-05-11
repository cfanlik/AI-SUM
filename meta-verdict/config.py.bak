"""
meta-verdict 配置
"""
from __future__ import annotations

# ── 数据路径 ──
import os
_env_path = "/opt/AI-SUM/.env"
def _read_env(key, default=""):
    try:
        for line in open(_env_path):
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return default

SUM_DB_PATH = _read_env("SUM_DB_PATH", "/opt/AI-SUM/select-sum.db")
SRC_DB_PATH = _read_env("SRC_DB_PATH", "/opt/select-coin/data/select.db")
REPORT_DIR  = "/opt/AI-SUM/report/meta"

# ── 积分权重 ──
MASTER_DIAMOND = 4
MASTER_RED     = 2
MASTER_YELLOW  = 1

OPUS_ACC_SCALE  = 0.04
OPUS_DIST_SCALE = 0.04

UNIFIED_SCORE = {
    "DIAMOND": 4,
    "RED":     2,
    "YELLOW":  1,
}

# unified 出货方向积分
UNIFIED_DIST_SCORE = {
    "SLOW_DIST":    -2,
    "WHALE_DUMP":   -3,
}

WHALE_HIGH   = 3
WHALE_MEDIUM = 2
WHALE_LOW    = 1

CB_SCORE = {
    "SQUEEZE_ACC_HIGH": 3,
    "STEALTH_ACC":      3,
    "SQUEEZE_ACC_MED":  2,
    "SQUEEZE_ACC_LOW":  1,
    "IRON_HOLD":        2,
    "DEATH_SPIRAL":    -5,
    "LIQUIDITY_CRISIS":-3,
    "PROFIT_EXIT":     -2,
    "BAG_PASSING":     -1,
}

# ── 仲裁阈值 ──
META_ACC_THRESHOLD  = 3.0
META_DIST_THRESHOLD = -2.0
META_TOP_N          = 20

# ── 趋势分析 ──
TREND_JUMP_THRESHOLD = 1.5    # 积分跃变阈值
ENGINE_HEALTH_MAX_HOURS = 3   # 引擎健康告警阈值（小时）

# ── hop2 庄控跟踪积分 (v5) ──
HOP2_ACC_BONUS = {
    "high":   1.5,   # hop2_acc_pct >= 30%
    "medium": 0.8,   # hop2_acc_pct >= 15%
    "low":    0.0,   # hop2_acc_pct < 15%
}
