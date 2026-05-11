"""
cost-basis-scan 全局配置
阈值优先从 .env 读取，否则使用默认值
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# ── .env 加载 ──
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)

# ── 数据库路径 ──
SRC_DB_PATH = os.getenv("SRC_DB_PATH", "/opt/select-coin/data/select.db")
SUM_DB_PATH = os.getenv("SUM_DB_PATH", "/opt/AI-SUM/data/select-sum.db")

# ── 报告输出 ──
REPORT_DIR = os.getenv("CB_REPORT_DIR", str(Path(__file__).resolve().parent.parent / "report" / "cost-basis"))

# ── 门控阈值 ──
G1_MIN_COST_HOLDERS = int(os.getenv("CB_G1_MIN_COST_HOLDERS", "5"))       # 有成本数据的地址最少数量
G3_LP_VETO_USD      = float(os.getenv("G2_LP_VETO_USD", "10000"))          # LP < $10K 否决
G3_LP_THIN_USD      = float(os.getenv("G2_LP_THIN_USD", "30000"))          # LP < $30K 降级
G4_DEAD_POOL_VL     = float(os.getenv("G3_DEAD_POOL_VL", "0.01"))
G4_DEAD_POOL_VOL    = float(os.getenv("G3_DEAD_POOL_VOL", "100"))
G5_PRICE_OUTLIER_MULT = float(os.getenv("CB_G5_OUTLIER_MULT", "100"))      # avg_price > price * 100 → 脏数据

# ── 四成本带阈值 (倍数基于现价) ──
ZONE_DEEP_UNDERWATER  = float(os.getenv("CB_ZONE_DEEP", "1.3"))   # 成本 > 现价×1.3 → 深水区
ZONE_SHALLOW_UNDERWATER = float(os.getenv("CB_ZONE_SHALLOW", "1.0"))  # 成本 在 现价×1.0~1.3 → 浅水区
ZONE_PROFIT           = float(os.getenv("CB_ZONE_PROFIT", "0.5"))  # 成本 在 现价×0.5~1.0 → 盈利区
# 成本 < 现价×0.5 → 暴利区 (Windfall)

# ── 信号阈值 ──
# C1: 水下逆势加仓
C1_WEIGHT = 7

# C2: 成本聚集（一致行动人）
C2_WEIGHT = 6
C2_CV_THRESHOLD = float(os.getenv("CB_C2_CV", "0.15"))        # CV < 0.15 触发

# C3: 暴利区坚定持有
C3_WEIGHT = 5
C3_DIAMOND_HOLD_PCT = float(os.getenv("CB_C3_HOLD_PCT", "50"))  # 暴利区不卖占比 ≥ 50%

# C4: 暴利出逃预警
C4_WEIGHT = 7

# C5: 高位派发
C5_WEIGHT = 5
C5_NEW_COST_LOW  = float(os.getenv("CB_C5_COST_LOW", "0.9"))   # 新地址成本 ≥ 现价×0.9
C5_NEW_COST_HIGH = float(os.getenv("CB_C5_COST_HIGH", "1.1"))  # 新地址成本 ≤ 现价×1.1

# C6: 流动性危机
C6_WEIGHT = 5
C6_WINDFALL_PCT_MIN = float(os.getenv("CB_C6_WINDFALL_PCT", "40"))  # 暴利区占比 > 40%
C6_LP_MAX_USD       = float(os.getenv("CB_C6_LP_MAX", "500000"))    # LP < $500K

# C7: 成本-持仓倒挂
C7_WEIGHT = 4

# C8: 暴利区 48h 净流出
C8_WEIGHT = 4
C8_NETFLOW_PCT = float(os.getenv("CB_C8_NETFLOW_PCT", "10"))  # 净流出 > 持仓 10% 触发

# ── 快照间距降权 ──
SNAPSHOT_GAP_HOURS_MAX = float(os.getenv("CB_SNAP_GAP_MAX", "48"))  # >48h 对 C1 降权

# ── 裁决阈值 ──
VERDICT_ACC_MIN = float(os.getenv("CB_VERDICT_ACC_MIN", "50"))    # ACC% ≥ 50% → ACCUMULATING
VERDICT_DIST_MIN = float(os.getenv("CB_VERDICT_DIST_MIN", "50"))  # DIST% ≥ 50% → DISTRIBUTING

# ── 重心漂移阈值 ──
GRAVITY_DRIFT_SIGNIFICANT = float(os.getenv("CB_GRAVITY_DRIFT", "0.1"))  # 重心变化 > 10% 视为显著漂移

# ── 信号总权重 ──
TOTAL_WEIGHT = C1_WEIGHT + C2_WEIGHT + C3_WEIGHT + C4_WEIGHT + C5_WEIGHT + C6_WEIGHT + C7_WEIGHT + C8_WEIGHT
ACC_WEIGHT_TOTAL  = C1_WEIGHT + C2_WEIGHT + C3_WEIGHT  # 18
DIST_WEIGHT_TOTAL = C4_WEIGHT + C5_WEIGHT + C8_WEIGHT   # 16
STRUCT_WEIGHT_TOTAL = C6_WEIGHT + C7_WEIGHT              # 9
