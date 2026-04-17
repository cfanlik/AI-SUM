"""
whale-scan — 配置
复用 AI-SUM .env 读取机制，庄控信号阈值。
"""
import os
from pathlib import Path

# ── .env 读取 ──
_ENV_FILE = Path(__file__).parent.parent / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        _k, _v = _k.strip(), _v.strip()
        if _k and _v and _k not in os.environ:
            os.environ[_k] = _v

# ── 数据库路径 ──
_SRC_DEFAULT = str(Path(r"C:\Users\Administrator\.gemini\antigravity\playground\select-coin\data\select.db"))
SRC_DB_PATH: str = os.environ.get("SRC_DB_PATH", _SRC_DEFAULT)

# ── S1 集中度阈值 ──
TOP2_HOLD_THRESHOLD = 50.0
TOP5_HOLD_THRESHOLD = 80.0
TOP10_HOLD_THRESHOLD = 90.0
TOP20_HOLD_THRESHOLD = 95.0
TOP2_DEX_MAX = 0.05
TOP2_WALLET_REQUIRED = True

# ── S2 漂移阈值 ──
WHALE1_DRIFT_MIN = 5.0    # #1 持仓Δ最小触发值 (%)
WHALE2_DRIFT_MIN = 3.0    # #2 持仓Δ最小触发值 (%)
MULTI_DRIFT_MIN = 3        # Top10中被动漂移地址最少数量
ACC_PUMP_PRICE_MIN = 30.0  # 联合条件：价格涨幅 (%)

# ── S3 反向吸筹阈值 ──
LOW_ACC_PCT = 20.0
LOW_COMPOSITE = 40.0
LOW_DEX_PCT = 60.0
ACC_FREEFALL_PCT = 80.0    # acc_pct 首末下降百分比

# ── S4 价格异动阈值 ──
PUMP_PCT_THRESHOLD = 100.0
SUSTAINED_PUMP_PCT = 30.0
MCAP_LIQ_THRESHOLD = 50.0

# ── S5 派发阈值 ──
DIST_KEYWORD = "近期派发"

# ── S6 锁仓阈值 ──
CONTRACT_HOLD_THRESHOLD = 20.0
TOP300_COVERAGE_THRESHOLD = 97.0

# ── 置信度分级 ──
LEVEL_HIGH = 70.0     # 🔴
LEVEL_MEDIUM = 50.0   # 🟠
LEVEL_LOW = 30.0      # 🟡

# ── 输出 ──
REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "report", "whale")
Path(REPORT_DIR).mkdir(parents=True, exist_ok=True)
MIN_SNAPSHOTS = 3
TOP_N_REPORT = 20
