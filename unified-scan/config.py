# ──────────────────────────────────────────────────────────
# 信号编码速查表 (Signal Code Reference)
# ──────────────────────────────────────────────────────────
# A1(DIAMOND/RED)  — BubbleMap 吸筹标签等级
# A2(YELLOW/RED)   — 二级吸筹指标（YELLOW=中等, RED=强）
# A4(CEX流出)      — 代币从 CEX 转出到链上 → 买入持有
# D1(CEX流入)      — 代币流入 CEX → 准备卖出
# D2(出货者)       — 检测到出货行为的地址
# D3(被动漂移)     — 持仓未变但价格下跌，被动承受亏损
# S1(极端集中)     — Top 地址持仓极度集中
# S2(M/L=Nx)       — 市值/流动性比 → 越高越脆弱
# S4(V/L=x)        — 换手效率 → V/L>10 极端换手（标记不计分）
# G2(LP=$x)        — LP 流动性门控 → <$30K降级, <$10K否决
# G3(死池)         — V/L<0.01 + Vol<$100 → 否决ACC信号
# ──────────────────────────────────────────────────────────

"""
unified-scan — 统一配置
三框架精华融合：master-scan(A1/A2/A3/G1) + opus-scan(A4/D1/D2/S3) + bigcoin(D3/S1/S2)
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

# ════════════════════════════════════════════════════════════
# 数据库路径
# ════════════════════════════════════════════════════════════
_SRC_DEFAULT = str(
    Path(r"C:\Users\Administrator\.gemini\antigravity\playground\select-coin\data\select.db")
)
SRC_DB_PATH: str = os.environ.get("SRC_DB_PATH", _SRC_DEFAULT)

_SUM_DEFAULT = str(Path(__file__).parent.parent / "data" / "select-sum.db")
SUM_DB_PATH: str = os.environ.get("SUM_DB_PATH", _SUM_DEFAULT)
Path(SUM_DB_PATH).parent.mkdir(parents=True, exist_ok=True)

# ════════════════════════════════════════════════════════════
# G1: DEX 质量门控
# ════════════════════════════════════════════════════════════
MIN_SIGNAL_DEX_PCT: float = float(os.environ.get("MIN_SIGNAL_DEX_PCT", "10.0"))

# ════════════════════════════════════════════════════════════
# A1: 钻石绞杀阈值
# ════════════════════════════════════════════════════════════
DIAMOND_INST_THRESHOLD: float = float(os.environ.get("DIAMOND_INST_THRESHOLD", "85.0"))
DIAMOND_DEX_THRESHOLD: float = float(os.environ.get("DIAMOND_DEX_THRESHOLD", "85.0"))
HIDDEN_WHALE_HOLD_THRESHOLD: float = float(os.environ.get("HIDDEN_WHALE_HOLD_THRESHOLD", "2.0"))

# ════════════════════════════════════════════════════════════
# A2: 地址聚合阈值
# ════════════════════════════════════════════════════════════
A2_ROSTER_YELLOW: float = 0.03   # 换手率 >3% → YELLOW
A2_ROSTER_RED: float = 0.08      # 换手率 >8% → RED
A2_NEW_ACC_RATIO: float = 0.30   # 新acc/旧acc >30% 辅助触发
A2_NEW_ACC_MIN_CNT: int = 5      # 新acc最少数量

# ════════════════════════════════════════════════════════════
# A3: 新鲸下场阈值
# ════════════════════════════════════════════════════════════
A3_ACC_SCORE_MIN: float = 72.0
A3_ONLY_BUY_RATIO: float = 0.80
A3_HOLD_PCT_MIN: float = 0.5     # 新鲸持仓≥0.5%

# ════════════════════════════════════════════════════════════
# A4/D1: CEX 流向阈值
# ════════════════════════════════════════════════════════════
CEX_OUTFLOW_DELTA: float = -3.0   # A4: CEX下降>3%
CEX_OUTFLOW_SLOPE: float = -0.2   # A4: 斜率<-0.2
CEX_INFLOW_DELTA: float = 5.0     # D1: CEX上升>5%
CEX_INFLOW_SLOPE: float = 0.3     # D1: 斜率>0.3

# ════════════════════════════════════════════════════════════
# D2: 出货者画像阈值
# ════════════════════════════════════════════════════════════
D2_SELL_BUY_RATIO: float = 3.0    # 卖/买比≥3 = 出货者
D2_FAKE_WHALE_MIN: int = 2        # 假鲸鱼≥2
D2_DIST_48H_MIN: int = 3          # 48h派发者≥3
D2_SELLER_HOLD_MIN: float = 1.0   # 出货者持仓≥1%

# ════════════════════════════════════════════════════════════
# D3: 被动漂移阈值
# ════════════════════════════════════════════════════════════
D3_DRIFT_MIN: float = 3.0         # 持仓涨>3%
D3_BUY_TOLERANCE: float = 10.0    # 买入金额变化容差($)
D3_MULTI_DRIFT_MIN: int = 3       # Top10中被动漂移≥3

# ════════════════════════════════════════════════════════════
# S1: 极端集中度阈值
# ════════════════════════════════════════════════════════════
S1_TOP2_HOLD: float = 50.0
S1_TOP2_DEX_MAX: float = 0.05
S1_TOP10_HOLD: float = 90.0

# ════════════════════════════════════════════════════════════
# S2: M/L 泡沫比
# ════════════════════════════════════════════════════════════
S2_MCAP_LIQ_THRESHOLD: float = 50.0

# ════════════════════════════════════════════════════════════
# S3: Gecko Pool 联网增强
# ════════════════════════════════════════════════════════════
S3_ENABLED: bool = True
S3_BUY_SELL_PERSON_THRESHOLD: float = 0.5  # <0.5 出货信号
S3_ACC_PERSON_THRESHOLD: float = 1.5       # ≥1.5 吸筹信号
GECKO_API_BASE: str = "https://api.geckoterminal.com/api/v2"
PROXY_URL: str = os.environ.get("PROXY_URL", "socks5://127.0.0.1:42000")
CHAIN_MAP = {"bsc": "bsc", "eth": "eth", "sol": "solana", "base": "base", "arb": "arbitrum"}

# ════════════════════════════════════════════════════════════
# 快照窗口
# ════════════════════════════════════════════════════════════
DEFAULT_SNAP_WINDOW: int = 4
MAX_HOURS_GAP: float = 30.0

# ════════════════════════════════════════════════════════════
# 前置过滤
# ════════════════════════════════════════════════════════════
MIN_ACC_HOLDERS: int = 3
MIN_SNAPSHOTS: int = 3
TOP_HOLDERS_N: int = 30


# ════════════════════════════════════════════════════════════
# S4: V/L 换手效率
# ════════════════════════════════════════════════════════════
S4_VL_HIGH_THRESHOLD: float = 10.0    # V/L > 10 → 极端换手（标记不计分）

# ════════════════════════════════════════════════════════════
# G2: LP 流动性门控
# ════════════════════════════════════════════════════════════
G2_LP_THIN_USD: float = 30000.0       # LP < $30K → 降级（DIAMOND/STRONG → MODERATE）
G2_LP_VETO_USD: float = 10000.0       # LP < $10K → 否决所有信号 → NEUTRAL

# ════════════════════════════════════════════════════════════
# G3: 死池检测
# ════════════════════════════════════════════════════════════
G3_DEAD_POOL_VL: float = 0.01        # V/L < 0.01
G3_DEAD_POOL_VOL: float = 100.0      # Vol24h < $100 → 联合触发死池否决
# ════════════════════════════════════════════════════════════
# 输出
# ════════════════════════════════════════════════════════════
REPORT_DIR: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "report", "unified")
Path(REPORT_DIR).mkdir(parents=True, exist_ok=True)
