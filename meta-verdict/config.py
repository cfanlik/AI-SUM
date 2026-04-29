"""
meta-verdict 配置
"""
import os
from pathlib import Path
from dotenv import load_dotenv

_env = Path(__file__).resolve().parent.parent / ".env"
if _env.exists():
    load_dotenv(_env)

SUM_DB_PATH = os.getenv("SUM_DB_PATH", "/opt/AI-SUM/data/select-sum.db")
REPORT_DIR  = os.getenv("META_REPORT_DIR",
    str(Path(__file__).resolve().parent.parent / "report" / "meta"))

# ── 仲裁积分权重 ──
# master-scan
MASTER_DIAMOND = 4
MASTER_RED     = 2
MASTER_YELLOW  = 1

# opus-scan  (acc_confidence / dist_confidence 按百分比折算)
OPUS_ACC_SCALE  = 0.04   # 66.7% × 0.04 = 2.67
OPUS_DIST_SCALE = 0.04

# whale-scan
WHALE_HIGH   = 3
WHALE_MEDIUM = 2
WHALE_LOW    = 1

# cost-basis-scan
CB_SCORE = {
    "SQUEEZE_ACC_HIGH": 3,
    "SQUEEZE_ACC_MED":  2,
    "SQUEEZE_ACC_LOW":  1,
    "STEALTH_ACC":      3,
    "IRON_HOLD":        2,
    "ACCUMULATING":     1,
    "DEATH_SPIRAL":    -5,
    "LIQUIDITY_CRISIS":-3,
    "PROFIT_EXIT":     -2,
    "BAG_PASSING":     -2,
    "DISTRIBUTING":    -1,
}

# unified-scan
UNIFIED_SCORE = {
    "DIAMOND": 4,
    "RED":     2,
    "YELLOW":  1,
}

# 裁决阈值
META_ACC_THRESHOLD  = 3.0   # 综合 ≥ 3 → 吸筹
META_DIST_THRESHOLD = -2.0  # 综合 ≤ -2 → 出货
META_TOP_N          = 20    # 报告展示 Top N
