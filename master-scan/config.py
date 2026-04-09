"""
AI-SUM V5 — 全局配置
所有阈值、路径、代理配置统一在此修改，不要在各模块内硬编码。
路径配置优先读取项目根目录的 .env 文件，回退到内置默认值。
"""
import os
from pathlib import Path

# ============================================================
# 读取 .env 配置（可选，不强依赖 python-dotenv）
# ============================================================
_ENV_FILE = Path(__file__).parent.parent / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        _k = _k.strip()
        _v = _v.strip()
        if _k and _v and _k not in os.environ:
            os.environ[_k] = _v

# ============================================================
# 数据库路径
# ============================================================
# 源库（只读）—— select-coin 采集写入的主数据库
# 直接访问原始文件，不复制副本
_SRC_DB_DEFAULT = str(
    Path(r"C:\Users\Administrator\.gemini\antigravity\playground\select-coin\data\select.db")
)
SRC_DB_PATH: str = os.environ.get("SRC_DB_PATH", _SRC_DB_DEFAULT)

# 独立分析库（读写）—— V5 追踪数据、diff 缓存、运行记录
# 路径由 .env 的 SUM_DB_PATH 控制，回退到同级 data/ 目录
_SUM_DB_DEFAULT = str(Path(__file__).parent.parent / "data" / "select-sum.db")
SUM_DB_PATH: str = os.environ.get("SUM_DB_PATH", _SUM_DB_DEFAULT)
# 确保目录存在
Path(SUM_DB_PATH).parent.mkdir(parents=True, exist_ok=True)

# ============================================================
# 快照分析窗口
# ============================================================
DEFAULT_SNAP_WINDOW: int = 4    # 默认分析最近 N 个快照（含最新）
MAX_HOURS_GAP: float     = 30.0  # 超过此间隔标注"数据间隔过大"

# ============================================================
# 模式 A — 地址聚合（Aggregation Pattern）
# ============================================================
PATTERN_A_ROSTER_YELLOW: float = 0.03   # 换手率黄色阈值（10h 内 Top300 换手 >3%）
PATTERN_A_ROSTER_RED:    float = 0.08   # 换手率红色阈值（>8%）
PATTERN_A_NEW_ACC_RATIO: float = 0.30   # 新acc地址 / 旧acc地址 > 30% 触发辅助条件
PATTERN_A_NEW_ACC_MIN_CNT: int = 5      # 新acc地址最少绝对数量（防止小数点噪声）

# ============================================================
# 模式 B — 新鲸下场（Fresh Whale Pattern）
# ============================================================
PATTERN_B_ACC_SCORE_MIN:   float = 72.0  # 新进入地址的最低平均 acc_score
PATTERN_B_ONLY_BUY_RATIO:  float = 0.80  # 新acc中纯买入比例 >= 80%
PATTERN_B_HOLD_PCT_YELLOW: float = 0.5   # 新鲸持仓合计黄色阈值 (%)
PATTERN_B_HOLD_PCT_RED:    float = 2.0   # 新鲸持仓合计红色阈值 (%)

# ============================================================
# 模式 C — 爆发前静默（Pre-Pump Silence）
# ============================================================
PATTERN_C_HOLD_RATIO_VS_MEDIAN: float = 1.20  # 最新acc持仓 >= 历史中位数 * 1.20
PATTERN_C_TURNOVER_MAX:         float = 0.02  # 换手率极低门槛 < 2%（V8.4 收紧）
PATTERN_C_ONLY_BUY_MIN:         float = 0.70  # 只买不卖比例 >= 70%
PATTERN_C_YELLOW_CONDITIONS:    int   = 3     # 满足 3/4 条件 → 黄色
PATTERN_C_RED_CONDITIONS:       int   = 3     # V8.4: 满足 3/4 + cond_4必须 → 红色

# ============================================================
# Watchlist 生命周期
# ============================================================
WATCHLIST_EXPIRE_SCANS: int = 5   # 连续 N 次无信号自动 EXPIRED
WATCHLIST_PUMP_GAIN:    float = 0.50  # 价格涨幅 > 50% 自动标记 PUMPED

# ============================================================
# 报告输出
# ============================================================
REPORT_DIR: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "report", "v5")

# ============================================================
# CMC 代理（与 v4.2.2 一致）
# ============================================================
CMC_PROXY: str = "socks5://127.0.0.1:18000"

# ============================================================
# 前置过滤（对齐 v4 逻辑）
# ============================================================
MIN_ACC_HOLDERS: int = 3   # 至少 3 个吸筹地址才纳入分析

# ============================================================
# 大户及隐庄判定阈值
# ============================================================
HIDDEN_WHALE_HOLD_THRESHOLD: float = float(os.environ.get("HIDDEN_WHALE_HOLD_THRESHOLD", "2.0"))

# ============================================================
# V8.4 全局信号质量门（DEX 底线）
# ============================================================
MIN_SIGNAL_DEX_PCT: float = float(os.environ.get("MIN_SIGNAL_DEX_PCT", "10.0"))

