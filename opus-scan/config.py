"""
opus-scan — 配置
复用 AI-SUM .env 读取机制，新增双维度评分阈值。
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
_SUM_DEFAULT = str(Path(__file__).parent.parent / "data" / "select-sum.db")
SUM_DB_PATH: str = os.environ.get("SUM_DB_PATH", _SUM_DEFAULT)
Path(SUM_DB_PATH).parent.mkdir(parents=True, exist_ok=True)

# ── 吸筹评分阈值 ──
ACC_HOLD_GROWTH_MIN    = 10.0
ACC_DEX_RATE_MIN       = 35.0
ACC_STRONG_BUYER_MIN   = 5
ACC_CEX_STABLE_RANGE   = 10.0
ACC_PRICE_PUMP_MAX     = 30.0

# ── 出货评分阈值 ──
DIST_CEX_DECLINE_MIN   = 20.0
DIST_SELL_BUY_RATIO    = 3.0
DIST_FAKE_WHALE_MIN    = 2
DIST_48H_SELLER_MIN    = 3
DIST_SELLER_HOLD_MIN   = 5.0
DIST_ACC_HOLD_LOW      = 15.0

# ── 联网 ──
GECKO_API_BASE = "https://api.geckoterminal.com/api/v2"
WEB_SEARCH_ENABLED = False  # 默认关闭，--online 开启
PROXY_URL = os.environ.get("PROXY_URL", "socks5://127.0.0.1:42000")

# Gecko Pool 增强阈值 (方案 A)
POOL_VOLUME_DECLINE_RATIO = 0.5   # 24h Volume < 7d均值×此值 → 量缩
POOL_LP_THIN_USD          = 50000 # LP < 此值 → 流动性不足
POOL_BUY_SELL_PERSON_MIN  = 1.5   # 买入人数/卖出人数 ≥ 此值 → 净买入
POOL_PRICE_7D_DROP_PCT    = -20.0 # 7d 跌幅% → 出货共振

CHAIN_MAP = {
    "bsc": "bsc", "eth": "eth", "sol": "solana",
    "base": "base", "arb": "arbitrum",
}

# ── 输出 ──
REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "report", "opus")
Path(REPORT_DIR).mkdir(parents=True, exist_ok=True)
TOP_N = 10
MIN_SNAPSHOTS = 3
TOP_HOLDERS_N = 100

# ═══ LP/VL 增强 (E1~E4) ═══
S4_VL_HIGH_THRESHOLD: float = 10.0    # V/L > 10 → 极端换手标记
G2_LP_THIN_USD: float = 30000.0       # LP < $30K → 降级
G2_LP_VETO_USD: float = 10000.0       # LP < $10K → 否决所有信号
G3_DEAD_POOL_VL: float = 0.01
G3_DEAD_POOL_VOL: float = 100.0
