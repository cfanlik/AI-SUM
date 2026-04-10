# -*- coding: utf-8 -*-
"""
全局吸筹扫描 v4 — 可执行脚本
=====================================
用途:  读取 BubbleMap 数据库，对所有代币做 8 维综合评分，输出 S/A/B/C/D 五级排名
入参:  DB_PATH (SQLite 数据库路径)
输出:  终端输出分级代币清单 + 各维度评分明细

使用方法:
  python -X utf8 accumulation_scan_v3.py 4
  python -X utf8 accumulation_scan_v3.py 4 --backfill-mcap

版本: v4.2.2  |  更新: 2026-03-27

⚠️ 强制规定: 修改本文件后，必须同步更新 AI-SUM/ 下所有关联 md 并在 changelog.md 记录:
  - SESSION_MEMORY.md
  - accumulation_scan_v3_methodology.md
  - sql_reference.md
  - changelog.md
"""

import sqlite3
import sys
import io
import os
import math
import time
import random
import ctypes
import csv
import json
import logging
from collections import defaultdict, Counter
from datetime import datetime
from pathlib import Path

# ====================================================================
# 配置
# ====================================================================
# 从本地 .env 读取 SRC_DB_PATH
_ENV_FILE = Path(__file__).parent / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        if _k.strip() and _v.strip() and _k.strip() not in os.environ:
            os.environ[_k.strip()] = _v.strip()

_SRC_DB_DEFAULT = r'C:\Users\Administrator\.gemini\antigravity\playground\select-coin\data\select.db'
DB_PATH = os.environ.get("SRC_DB_PATH", _SRC_DB_DEFAULT)

# 评分权重 v4.0 (8 维, 可调参)
W_ACC_PCT    = 0.20   # d1 吸筹占比 (基于真实用户)
W_AVG_SCORE  = 0.15   # d2 吸筹均分
W_HOLD_CTL   = 0.14   # d3 持仓控制度 (最新快照口径)
W_SELL_SUPP  = 0.12   # d4 卖出抑制 (只买不卖比例)
W_TREND      = 0.06   # d5 趋势稳定性 (v4: 时间衰减+方向一致性)
W_SIG_QUAL   = 0.13   # d6 信号质量 (加权)
W_CONC       = 0.10   # d7 集中度 (越分散越高)
W_REALTIME   = 0.10   # d8 实时活跃度 (Δhold + 新地址涌入)

# 信号质量权重表 — 按 BubbleMap 信号标签类型
SIGNAL_WEIGHTS = {
    "大额净流入":  5.0,   # Top5% 大资金
    "只买不卖":    4.0,   # 零卖出
    "买>>卖":      3.0,   # 买入压倒性
    "卖<":         3.0,   # 极低卖压 (匹配 "卖<10%买")
    "巨仓":        3.0,   # ≥1% 持仓
    "独立地址":    2.5,   # 非关联
    "大仓":        2.0,   # 0.5-1% 持仓
    "频繁买入":    2.0,   # ≥10 次
    "净流入":      1.5,   # Top20%
    "多次买入":    1.0,   # 5-9 次
}
SIG_QUALITY_MAX = 25.0  # 信号质量满分阈值

# 五级阈值
LEVEL_S = 75
LEVEL_A = 60
LEVEL_B = 45
LEVEL_C = 30

# 最小吸筹地址数 (过滤噪声)
MIN_ACC_HOLDERS = 3

MODE_LABELS = {
    1: "全部等级",
    2: "仅看 S/A 级",
    3: "仅看 S 级",
    4: "仅看汇总统计",
}

EXPORT_DIR = Path(__file__).resolve().parent / "exports"
REPORT_DIR = Path(__file__).resolve().parent / "report"

# select-coin 项目 .env 路径 (读取 CMC_API_KEY)
SELECT_COIN_ENV = Path(DB_PATH).parent.parent / '.env'

# CMC API 配置
CMC_BASE = "https://pro-api.coinmarketcap.com"
CMC_HTTP_TIMEOUT = 25
CMC_MAX_RETRIES = 5
CMC_RETRY_BACKOFF = 1.7
CMC_RATE_INTERVAL = 60.0 / 28  # 28 req/min
CMC_PROXY_OVERRIDE = "socks5://127.0.0.1:18000"

logger = logging.getLogger("ai-sum")

# ====================================================================
# 工具函数
# ====================================================================

def score_acc_pct(pct: float) -> float:
    """维度1: 吸筹占比得分。80% 占比 = 满分"""
    return min(100, pct * 1.25)

def score_avg_acc(avg: float) -> float:
    """维度2: 吸筹均分得分。55 是门槛，100 是满分"""
    if not avg:
        return 0
    return max(0, min(100, (avg - 55) / 45 * 100))

def score_hold_ctl(hold_pct: float) -> float:
    """维度3: 持仓控制度 (v3.3: 最新快照口径)。40% 供应 = 满分"""
    return min(100, hold_pct * 2.5)

def score_sell_supp(only_buy_cnt: int, acc_cnt: int) -> float:
    """维度4: 卖出抑制。66.7% 只买不卖 = 满分"""
    if acc_cnt <= 0:
        return 0
    pct = only_buy_cnt / acc_cnt * 100
    return min(100, pct * 1.5)

def score_trend(trend: dict | None) -> float:
    """维度5: 趋势稳定性 v4.0。时间衰减加权 + 方向一致性 + 近期加速度。"""
    if not trend:
        return 30.0  # 单快照给中性分
    values = trend.get('values', [])
    n = len(values)
    if n < 2:
        return 30.0

    # 1. 近期加权稳定性 (最近 3 次快照权重 3x, 其余 1x)
    weights = [1.0] * max(0, n - 3) + [3.0] * min(3, n)
    w_sum = sum(weights)
    wmean = sum(v * w for v, w in zip(values, weights)) / w_sum
    if wmean > 0:
        wvar = sum(w * (v - wmean) ** 2 for v, w in zip(values, weights)) / w_sum
        stability = max(0, 1 - math.sqrt(wvar) / wmean)
    else:
        stability = 0

    # 2. 方向一致性 (连续非递减快照占比)
    ups = sum(1 for i in range(1, n) if values[i] >= values[i - 1])
    consistency = ups / (n - 1)

    # 3. 综合: 稳定性 40 + 一致性 35 + 近期加速度 25
    base = stability ** 2 * 40
    base += consistency * 35

    # 近期加速: 最近 3 次均值 vs 往期均值
    if n >= 4:
        recent_avg = sum(values[-3:]) / 3
        old_avg = sum(values[:-3]) / max(1, n - 3)
        if old_avg > 0:
            accel = (recent_avg - old_avg) / old_avg
            base += min(25, max(0, accel * 50))
        elif recent_avg > 0:
            base += 25  # 从 0 变正 = 最大加速

    return min(100, base)

def score_sig_quality(quality_score: float) -> float:
    """维度6: 信号质量。基于信号类型加权而非简单计数。"""
    return min(100, quality_score)

def score_concentration(top1: float, top5: float, acc_h: int) -> float:
    """维度7: 集中度评分。越分散得分越高。"""
    if acc_h < 5:
        return 50.0  # 样本太少给中性分
    # top1 低于 5% 为极好 (100), 高于 50% 为极差 (0)
    t1_score = max(0, min(100, (50 - top1) / 45 * 100))
    # top5 低于 15% 为极好 (100), 高于 70% 为极差 (0)
    t5_score = max(0, min(100, (70 - top5) / 55 * 100))
    return t1_score * 0.4 + t5_score * 0.6

def score_realtime_activity(activity: dict | None) -> float:
    """维度8: 实时活跃度。基于跨快照 Δhold + 新地址涌入率。"""
    if not activity:
        return 50.0  # 中性分

    # 1. Δhold: 吸筹持仓变化 (最新 vs ~72h 前), [-2%, +2%] → [0, 50]
    delta_hold = activity.get('delta_acc_hold', 0)
    hold_score = min(50, max(0, (delta_hold + 2) / 4 * 50))

    # 2. 新地址涌入率 (5% 新增率 = 满分 50)
    new_rate = activity.get('new_acc_rate', 0)
    inflow_score = min(50, new_rate * 1000)

    return min(100, hold_score + inflow_score)


# ====================================================================
# CMC 市值回填
# ====================================================================

def _load_cmc_api_key() -> str:
    """从 select-coin/.env 读取 CMC_API_KEY。"""
    if SELECT_COIN_ENV.exists():
        for line in SELECT_COIN_ENV.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line.startswith('CMC_API_KEY=') and not line.startswith('#'):
                val = line.split('=', 1)[1].strip()
                if val:
                    return val
    # 回退: 环境变量
    return os.environ.get('CMC_API_KEY', '')


def _get_proxy() -> str | None:
    """优先使用 AI-SUM 固定代理，其次回退到 select-coin config。"""
    override = os.environ.get("AI_SUM_CMC_PROXY", CMC_PROXY_OVERRIDE).strip()
    if override:
        return override
    try:
        sys.path.insert(0, str(SELECT_COIN_ENV.parent))
        from config import get_proxy
        return get_proxy()
    except Exception:
        return None


class _CMCRateLimiter:
    def __init__(self):
        self.last_time = 0.0
    def wait(self):
        now = time.time()
        if self.last_time:
            delta = now - self.last_time
            if delta < CMC_RATE_INTERVAL:
                time.sleep(CMC_RATE_INTERVAL - delta)
        self.last_time = time.time()


def _cmc_get(limiter: _CMCRateLimiter, api_key: str, path: str,
             params: dict) -> dict:
    """CMC API GET 请求（限速+重试+代理）。"""
    import httpx
    url = f"{CMC_BASE}{path}"
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": api_key}
    proxy = _get_proxy()

    for attempt in range(1, CMC_MAX_RETRIES + 1):
        limiter.wait()
        try:
            r = httpx.get(url, headers=headers, params=params,
                          proxy=proxy, verify=False, timeout=CMC_HTTP_TIMEOUT)
        except Exception as e:
            sleep = (CMC_RETRY_BACKOFF ** attempt) + random.random()
            print(f"  ⚠️ CMC 网络错误: {e} | {sleep:.1f}s 后重试")
            time.sleep(sleep)
            continue

        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            sleep = int(ra) + 1 if ra and ra.isdigit() else 60
            print(f"  ⚠️ CMC 限速 429 | 等待 {sleep}s")
            time.sleep(sleep)
            continue

        if r.status_code >= 500:
            sleep = (CMC_RETRY_BACKOFF ** attempt) + random.random()
            print(f"  ⚠️ CMC 服务器 {r.status_code} | {sleep:.1f}s 后重试")
            time.sleep(sleep)
            continue

        if r.status_code == 400:
            # 400 "No items found" = 代币未收录 CMC，静默跳过
            return {}

        if r.status_code != 200:
            print(f"  ❌ CMC HTTP {r.status_code}: {r.text[:200]}")
            return {}

        data = r.json()
        status = data.get("status", {}) or {}
        if (status.get("error_code") or 0) != 0:
            # "No items found" 等非致命错误静默返回空
            return {}
        return data

    print(f"  ❌ CMC 重试耗尽: {path}")
    return {}


def backfill_market_cap(conn: sqlite3.Connection) -> int:
    """从 CMC API 批量回填 token_names.market_cap_usd。
    流程:
      1) 读取缺失市值的代币
      2) 逐个通过 /v2/cryptocurrency/info?address=合约 获取 CMC ID
      3) 批量通过 /v2/cryptocurrency/quotes/latest?id=.. 获取市值
      4) UPDATE token_names.market_cap_usd
    返回成功回填数量。
    """
    api_key = _load_cmc_api_key()
    if not api_key:
        print("  ℹ️ 未配置 CMC_API_KEY，跳过市值回填")
        return 0

    cursor = conn.cursor()
    missing = cursor.execute("""
        SELECT chain, token_address FROM token_names
        WHERE market_cap_usd IS NULL OR market_cap_usd <= 0
    """).fetchall()

    if not missing:
        print("  ✅ 所有代币已有市值数据")
        return 0

    print(f"  📊 CMC 市值回填: {len(missing)} 个代币缺失市值")
    proxy = _get_proxy()
    print(f"  🌐 CMC 代理: {proxy or '直连'}")
    limiter = _CMCRateLimiter()

    # Step 1: 通过合约地址获取 CMC ID
    addr_to_cmcid: dict[tuple[str, str], int] = {}
    resolved = 0
    not_found = 0
    total_missing = len(missing)
    for idx, (chain, addr) in enumerate(missing, 1):
        if addr_to_cmcid.get((chain, addr)):
            continue
        data = _cmc_get(limiter, api_key,
                        "/v2/cryptocurrency/info",
                        {"address": addr})
        cmc_data = data.get("data", {})
        if cmc_data:
            for cmc_id_str, info_obj in cmc_data.items():
                if isinstance(info_obj, list):
                    info_obj = info_obj[0] if info_obj else {}
                cmc_id = info_obj.get("id")
                if cmc_id:
                    addr_to_cmcid[(chain, addr)] = int(cmc_id)
                    resolved += 1
                break
        else:
            not_found += 1
        # 每 50 个或完成时打印进度
        if idx % 50 == 0 or idx == total_missing:
            print(f"    进度: {idx}/{total_missing} | 已找到={resolved} 未收录={not_found}")

    if not addr_to_cmcid:
        print(f"  ⚠️ 未能解析任何 CMC ID（已尝试 {len(missing)} 个地址）")
        return 0

    print(f"  ✅ 解析到 {len(addr_to_cmcid)} 个 CMC ID，开始获取市值")

    # Step 2: 批量获取市值 (每批 100 个 CMC ID)
    unique_ids = list(set(addr_to_cmcid.values()))
    id_to_mcap: dict[int, float] = {}

    for i in range(0, len(unique_ids), 100):
        batch = unique_ids[i:i + 100]
        data = _cmc_get(limiter, api_key,
                        "/v2/cryptocurrency/quotes/latest",
                        {"id": ",".join(map(str, batch)), "convert": "USD"})
        quotes = data.get("data", {})
        for cmc_id_str, info in quotes.items():
            if isinstance(info, list):
                info = info[0] if info else {}
            quote = (info.get("quote") or {}).get("USD", {})
            mcap = quote.get("market_cap")
            if mcap and mcap > 0:
                id_to_mcap[int(cmc_id_str)] = mcap

    if not id_to_mcap:
        print("  ⚠️ CMC 未返回任何市值数据")
        return 0

    # Step 3: 写入数据库
    updated = 0
    for (chain, addr), cmc_id in addr_to_cmcid.items():
        mcap = id_to_mcap.get(cmc_id)
        if mcap and mcap > 0:
            cursor.execute("""
                UPDATE token_names SET market_cap_usd = ?, updated_at = CURRENT_TIMESTAMP
                WHERE chain = ? AND token_address = ?
            """, (mcap, chain, addr))
            updated += 1

    conn.commit()
    print(f"  ✅ 市值回填完成: {updated}/{len(missing)} 个代币已更新")
    return updated


# ====================================================================
# 评分持久化 (token_scores 表)
# ====================================================================

def ensure_scores_table(conn: sqlite3.Connection) -> None:
    """创建 token_scores 表（如不存在）。"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS token_scores (
            chain           TEXT NOT NULL,
            token_address   TEXT NOT NULL,
            scan_time       TEXT NOT NULL,
            composite       REAL DEFAULT 0,
            level           TEXT DEFAULT '',
            d1 REAL, d2 REAL, d3 REAL, d4 REAL,
            d5 REAL, d6 REAL, d7 REAL, d8 REAL,
            acc_h           INTEGER DEFAULT 0,
            acc_pct_real    REAL DEFAULT 0,
            cluster_ratio   REAL DEFAULT 0,
            delta_hold      REAL DEFAULT 0,
            new_acc_cnt     INTEGER DEFAULT 0,
            direct_dex_acc_pct REAL DEFAULT 0,
            hop2_dex_acc_pct   REAL DEFAULT 0,
            gmgn_pass_acc_pct  REAL DEFAULT 0,
            gmgn_double_acc_pct REAL DEFAULT 0,
            review_priority TEXT DEFAULT '',
            structure_tags  TEXT DEFAULT '',
            snap_range_min  TEXT DEFAULT '',
            snap_range_max  TEXT DEFAULT '',
            PRIMARY KEY (chain, token_address, scan_time)
        );
        CREATE INDEX IF NOT EXISTS idx_token_scores_lookup
            ON token_scores(chain, token_address);
        CREATE INDEX IF NOT EXISTS idx_token_scores_time
            ON token_scores(scan_time);
    """)
    existing_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(token_scores)").fetchall()
    }
    for col_name, col_def in (
        ("direct_dex_acc_pct", "REAL DEFAULT 0"),
        ("hop2_dex_acc_pct", "REAL DEFAULT 0"),
        ("gmgn_pass_acc_pct", "REAL DEFAULT 0"),
        ("gmgn_double_acc_pct", "REAL DEFAULT 0"),
    ):
        if col_name not in existing_cols:
            conn.execute(f"ALTER TABLE token_scores ADD COLUMN {col_name} {col_def}")
    conn.commit()


def save_scores_to_db(conn: sqlite3.Connection, results: list[dict],
                      scan_time: str,
                      snap_ranges: dict[tuple[str, str], tuple[str, str]]) -> int:
    """将评分结果批量写入 token_scores 表。返回写入行数。"""
    cursor = conn.cursor()
    rows = []
    for r in results:
        key = (r['chain'], r['addr'])
        smin, smax = snap_ranges.get(key, ('', ''))
        rows.append((
            r['chain'], r['addr'], scan_time,
            r['composite'], r['level'],
            r['d1'], r['d2'], r['d3'], r['d4'],
            r['d5'], r['d6'], r['d7'], r['d8'],
            r['acc_h'], r['acc_pct_real'],
            r.get('cluster_ratio', 0),
            r.get('delta_hold', 0),
            r.get('new_acc_cnt', 0),
            r.get('direct_dex_acc_pct', 0),
            r.get('hop2_dex_acc_pct', 0),
            r.get('gmgn_pass_acc_pct', 0),
            r.get('gmgn_double_acc_pct', 0),
            r.get('review_priority', ''),
            ' | '.join(r.get('structure_tags', [])),
            smin, smax,
        ))

    cursor.executemany("""
        INSERT OR REPLACE INTO token_scores
            (chain, token_address, scan_time,
             composite, level,
             d1, d2, d3, d4, d5, d6, d7, d8,
             acc_h, acc_pct_real, cluster_ratio,
             delta_hold, new_acc_cnt,
             direct_dex_acc_pct, hop2_dex_acc_pct,
             gmgn_pass_acc_pct, gmgn_double_acc_pct,
             review_priority, structure_tags,
             snap_range_min, snap_range_max)
        VALUES (?,?,?, ?,?, ?,?,?,?,?,?,?,?, ?,?,?, ?,?, ?,?,?,?, ?,?, ?,?)
    """, rows)
    conn.commit()
    return len(rows)


def fetch_snapshot_ranges(cursor: sqlite3.Cursor
                          ) -> dict[tuple[str, str], tuple[str, str]]:
    """获取每个代币的 BubbleMap 快照时间范围 (min, max)。"""
    cursor.execute("""
        SELECT chain, token_address,
               MIN(snapshot_time) as snap_min,
               MAX(snapshot_time) as snap_max
        FROM bubblemap_holders
        GROUP BY chain, token_address
    """)
    return {
        (chain, addr): (smin, smax)
        for chain, addr, smin, smax in cursor.fetchall()
    }


def compute_composite(d1, d2, d3, d4, d5, d6, d7, d8) -> float:
    """计算综合评分 (8 维)"""
    return (d1 * W_ACC_PCT +
            d2 * W_AVG_SCORE +
            d3 * W_HOLD_CTL +
            d4 * W_SELL_SUPP +
            d5 * W_TREND +
            d6 * W_SIG_QUAL +
            d7 * W_CONC +
            d8 * W_REALTIME)

def get_level(composite: float) -> tuple[str, str]:
    """根据综合分返回等级和标签"""
    if composite >= LEVEL_S: return "S", "极强吸筹"
    if composite >= LEVEL_A: return "A", "强吸筹"
    if composite >= LEVEL_B: return "B", "中等吸筹"
    if composite >= LEVEL_C: return "C", "弱吸筹"
    return "D", "微弱信号"

def calc_stability(values: list[int]) -> tuple[float, int]:
    """计算变异系数稳定性和趋势方向"""
    mean_v = sum(values) / len(values)
    if mean_v > 0:
        std = math.sqrt(sum((x - mean_v) ** 2 for x in values) / len(values))
        stability = max(0, 1 - std / mean_v)
    else:
        stability = 0
    direction = values[-1] - values[0]
    return round(stability, 4), direction

def enable_windows_utf8() -> None:
    """尽量修复 Windows cmd 下的中文乱码问题。"""
    if sys.platform != "win32":
        return

    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
    except Exception:
        pass

    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            if stream_name == "stdout" and hasattr(stream, "buffer"):
                sys.stdout = io.TextIOWrapper(stream.buffer, encoding="utf-8", errors="replace")
            elif stream_name == "stderr" and hasattr(stream, "buffer"):
                sys.stderr = io.TextIOWrapper(stream.buffer, encoding="utf-8", errors="replace")

def parse_cli(argv: list[str]) -> tuple[int, bool]:
    """解析命令行参数。

    - `1-4`: 输出模式
    - `--backfill-mcap`: 显式开启 CMC 市值回填
    """
    mode: int | None = None
    backfill_mcap = False

    for raw in argv[1:]:
        raw = raw.strip()
        if raw in {"1", "2", "3", "4"}:
            if mode is not None:
                raise SystemExit("模式参数只能提供一个 1-4。")
            mode = int(raw)
            continue
        if raw == "--backfill-mcap":
            backfill_mcap = True
            continue
        if raw in {"-h", "--help"}:
            raise SystemExit(
                "用法: python -X utf8 accumulation_scan_v3.py [1-4] [--backfill-mcap]\n"
                "说明: 默认跳过 CMC 市值回填，仅在显式传入 --backfill-mcap 时执行。"
            )
        raise SystemExit(
            "参数仅支持 1-4 与 --backfill-mcap。"
            "示例: python -X utf8 accumulation_scan_v3.py 4 --backfill-mcap"
        )

    if mode is not None:
        return mode, backfill_mcap

    # 默认采用静默模式 4 (汇总统计)，对齐 V5 体验
    return 4, backfill_mcap

def filter_results(results: list[dict], mode: int) -> list[dict]:
    if mode == 1:
        return results
    if mode == 2:
        return [r for r in results if r['level'] in {'S', 'A'}]
    if mode == 3:
        return [r for r in results if r['level'] == 'S']
    return []

def export_results(results: list[dict], stats: dict) -> tuple[Path, Path]:
    """导出扫描结果到 CSV 和 JSON。"""
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = EXPORT_DIR / f"accumulation_scan_v3_{stamp}.csv"
    json_path = EXPORT_DIR / f"accumulation_scan_v3_{stamp}.json"

    export_rows = []
    for r in results:
        row = dict(r)
        trend = row.pop("trend", None)
        row["structure_tags"] = " | ".join(row.get("structure_tags", []))
        row["trend_stability"] = trend["stability"] if trend else None
        row["trend_direction"] = trend["dir"] if trend else None
        row["trend_snaps"] = trend["snaps"] if trend else row.get("snap_count")
        export_rows.append(row)

    fieldnames = [
        "level", "label", "composite", "chain", "name", "symbol", "addr", "mcap",
        "total_h", "real_users", "non_real", "acc_h", "acc_pct_real", "avg_acc",
        "acc_hold", "acc_buy", "acc_sell", "acc_net", "only_buy", "sell_buy_ratio",
        "snapshot", "snap_count", "cex_hold",
        "latest_total_h", "latest_real_users", "latest_acc_h", "latest_acc_pct_real",
        "latest_acc_hold", "latest_avg_acc", "latest_only_buy_pct",
        "top1_acc_hold", "top3_acc_hold", "top5_acc_hold",
        "acc_top100_pct", "acc_rest_pct",
        "cluster_ratio", "eff_acc_h", "delta_hold", "new_acc_cnt",
        "direct_dex_acc_pct", "hop2_dex_acc_pct",
        "gmgn_pass_acc_pct", "gmgn_double_acc_pct",
        "review_priority", "structure_tags",
        "d1", "d2", "d3", "d4", "d5", "d6", "d7", "d8",
        "trend_stability", "trend_direction", "trend_snaps",
    ]

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(export_rows)

    payload = {
        "meta": {
            "version": "v4.2.2",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "db_path": DB_PATH,
            "total_tokens": len(results),
            "stats": stats,
        },
        "results": export_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return csv_path, json_path


def fetch_token_aggregates(cursor: sqlite3.Cursor) -> list[tuple]:
    """一次性聚合代币级指标，再按主键关联 token_names。"""
    cursor.execute("""
        WITH token_agg AS (
            SELECT
                b.chain,
                b.token_address,
                COUNT(*) as total_holders,
                SUM(CASE WHEN b.is_cex=0 AND b.is_dex=0 AND b.is_contract=0
                         AND b.is_supernode=0 THEN 1 ELSE 0 END) as real_users,
                SUM(CASE WHEN b.is_cex=1 OR b.is_dex=1 OR b.is_contract=1
                         OR b.is_supernode=1 THEN 1 ELSE 0 END) as non_real,
                SUM(CASE WHEN b.is_accumulating=1 THEN 1 ELSE 0 END) as acc_holders,
                ROUND(100.0 * SUM(CASE WHEN b.is_accumulating=1 THEN 1 ELSE 0 END) /
                      NULLIF(SUM(CASE WHEN b.is_cex=0 AND b.is_dex=0 AND b.is_contract=0
                           AND b.is_supernode=0 THEN 1 ELSE 0 END), 0), 1) as acc_pct_real,
                ROUND(AVG(CASE WHEN b.is_accumulating=1 THEN b.acc_score
                          ELSE NULL END), 2) as avg_acc_score,
                ROUND(SUM(CASE WHEN b.is_accumulating=1 THEN b.hold_percentage
                          ELSE 0 END), 2) as acc_hold_pct,
                ROUND(SUM(CASE WHEN b.is_accumulating=1 THEN b.buy_amt_usd
                          ELSE 0 END), 2) as acc_buy,
                ROUND(SUM(CASE WHEN b.is_accumulating=1 THEN b.sell_amt_usd
                          ELSE 0 END), 2) as acc_sell,
                ROUND(SUM(CASE WHEN b.is_accumulating=1 THEN b.net_inflow
                          ELSE 0 END), 2) as acc_net,
                SUM(CASE WHEN b.is_accumulating=1 AND b.sell_amt_usd=0 AND b.buy_amt_usd>0
                    THEN 1 ELSE 0 END) as only_buy_cnt,
                MAX(b.snapshot_time) as latest_snapshot,
                COUNT(DISTINCT b.snapshot_time) as snap_count,
                ROUND(SUM(CASE WHEN b.is_cex=1 OR b.is_contract=1 THEN b.hold_percentage
                          ELSE 0 END), 2) as cex_contract_hold
            FROM bubblemap_holders b
            GROUP BY b.chain, b.token_address
            HAVING acc_holders >= ?
        )
        SELECT
            a.chain,                                                                    -- 0
            a.token_address,                                                            -- 1
            COALESCE(t.name, '未知') as token_name,                                      -- 2
            COALESCE(t.symbol, '?') as token_symbol,                                     -- 3
            t.market_cap_usd,                                                           -- 4
            a.total_holders,                                                            -- 5
            a.real_users,                                                               -- 6
            a.non_real,                                                                 -- 7
            a.acc_holders,                                                              -- 8
            a.acc_pct_real,                                                             -- 9
            a.avg_acc_score,                                                            -- 10
            a.acc_hold_pct,                                                             -- 11
            a.acc_buy,                                                                  -- 12
            a.acc_sell,                                                                 -- 13
            a.acc_net,                                                                  -- 14
            a.only_buy_cnt,                                                             -- 15
            a.latest_snapshot,                                                          -- 16
            a.snap_count,                                                               -- 17
            a.cex_contract_hold                                                         -- 18
        FROM token_agg a
        LEFT JOIN token_names t
            ON a.chain = t.chain
           AND a.token_address = t.token_address
    """, (MIN_ACC_HOLDERS,))
    return cursor.fetchall()


def fetch_trend_data(cursor: sqlite3.Cursor) -> dict[tuple[str, str], dict]:
    """批量读取所有快照，再在 Python 侧按代币计算趋势。"""
    cursor.execute("""
        SELECT
            chain,
            token_address,
            snapshot_time,
            SUM(CASE WHEN is_accumulating=1 THEN 1 ELSE 0 END) as acc
        FROM bubblemap_holders
        GROUP BY chain, token_address, snapshot_time
        ORDER BY chain, token_address, snapshot_time
    """)

    snapshots_by_token: defaultdict[tuple[str, str], list[int]] = defaultdict(list)
    for chain, addr, _snapshot_time, acc in cursor.fetchall():
        snapshots_by_token[(chain, addr)].append(acc)

    trend_data = {}
    for key, values in snapshots_by_token.items():
        if len(values) < 2:
            continue
        stability, direction = calc_stability(values)
        trend_data[key] = {
            'stability': stability,
            'dir': direction,
            'snaps': len(values),
            'values': values,  # v4: 保留原始序列供 d5 时间衰减使用
        }
    return trend_data


def _calc_signal_quality(signals_str: str) -> float:
    """计算单条 acc_signals 的质量加权分。"""
    total_weight = 0.0
    for tag in signals_str.split(','):
        tag_clean = tag.strip()
        if not tag_clean:
            continue
        for key, weight in SIGNAL_WEIGHTS.items():
            if key in tag_clean:
                total_weight += weight
                break
    return min(100, total_weight / SIG_QUALITY_MAX * 100)


def fetch_signal_quality(cursor: sqlite3.Cursor) -> dict[tuple[str, str], float]:
    """批量计算每个代币的平均信号质量分（加权）。"""
    cursor.execute("""
        SELECT chain, token_address, acc_signals
        FROM bubblemap_holders
        WHERE is_accumulating=1
          AND acc_signals IS NOT NULL
          AND acc_signals != ''
    """)
    # 按代币聚合
    token_scores: defaultdict[tuple[str, str], list[float]] = defaultdict(list)
    for chain, addr, signals_str in cursor.fetchall():
        q = _calc_signal_quality(signals_str)
        token_scores[(chain, addr)].append(q)

    return {
        key: round(sum(scores) / len(scores), 2)
        for key, scores in token_scores.items()
    }


def fetch_latest_structure(cursor: sqlite3.Cursor) -> dict[tuple[str, str], dict]:
    """提取最新快照结构指标 + 聚类检测 + entity_id 去重 + 跨快照活跃度 (v4)。"""

    # ── 最新 + 第 4 个快照 (≈72h 跨度) 的原始数据 ──
    cursor.execute("""
        WITH ranked_snapshots AS (
            SELECT
                chain, token_address, snapshot_time,
                ROW_NUMBER() OVER (
                    PARTITION BY chain, token_address
                    ORDER BY snapshot_time DESC
                ) AS rn
            FROM (
                SELECT DISTINCT chain, token_address, snapshot_time
                FROM bubblemap_holders
            )
        ),
        target_snapshots AS (
            SELECT chain, token_address, snapshot_time, rn
            FROM ranked_snapshots
            WHERE rn = 1 OR rn = 4
        )
        SELECT
            b.chain,
            b.token_address,
            b.snapshot_time,
            b.wallet_address,
            b.rank,
            b.hold_percentage,
            b.acc_score,
            b.buy_amt_usd,
            b.sell_amt_usd,
            b.is_accumulating,
            b.is_cex,
            b.is_dex,
            b.is_contract,
            b.is_supernode,
            b.entity_id,
            b.inbound_addresses,
            b.outbound_addresses,
            b.dex_ratio,
            b.swap_in_value,
            b.dex_ratio_hop2,
            b.gmgn_verified,
            ts.rn
        FROM bubblemap_holders b
        JOIN target_snapshots ts
          ON b.chain = ts.chain
         AND b.token_address = ts.token_address
         AND b.snapshot_time = ts.snapshot_time
        ORDER BY b.chain, b.token_address, ts.rn, b.hold_percentage DESC
    """)

    # ── 按代币分组处理 ──
    token_snaps: dict[tuple[str, str], dict[int, list[tuple]]] = defaultdict(lambda: defaultdict(list))
    for row in cursor.fetchall():
        key = (row[0], row[1])
        rn = row[21]  # 1=最新, 4=~72h 前
        token_snaps[key][rn].append(row)

    latest_structure = {}
    for (chain, addr), snap_dict in token_snaps.items():
        latest_rows = snap_dict.get(1, [])
        prev_rows = snap_dict.get(4, [])  # 第 4 个快照 ≈ 72h 前

        if not latest_rows:
            continue

        # ── 基础统计 (最新快照) ──
        latest_total_h = len(latest_rows)
        latest_real_users = 0
        latest_acc_h = 0
        latest_acc_hold = 0.0
        latest_avg_scores = []
        only_buy_cnt = 0
        acc_top100 = 0
        acc_rest = 0
        direct_dex_cnt = 0
        hop2_dex_cnt = 0
        gmgn_pass_cnt = 0
        gmgn_double_cnt = 0
        dex_ratio_vals = []
        hop2_ratio_vals = []
        acc_rows_data = []  # (wallet, hold%, buy, sell, entity_id, inbound, outbound, dex_ratio, swap_in, hop2, gmgn)

        for row in latest_rows:
            is_real = (row[10] == 0 and row[11] == 0 and row[12] == 0 and row[13] == 0)
            if is_real:
                latest_real_users += 1
            if row[9] == 1:  # is_accumulating
                latest_acc_h += 1
                latest_acc_hold += row[5] or 0
                if row[6]:
                    latest_avg_scores.append(row[6])
                if (row[8] or 0) == 0 and (row[7] or 0) > 0:
                    only_buy_cnt += 1
                rank = row[4] or 0
                if rank <= 100:
                    acc_top100 += 1
                else:
                    acc_rest += 1
                dex_ratio = row[17]
                swap_in_value = row[18] or 0
                hop2_ratio = row[19]
                gmgn_verified = row[20]
                if dex_ratio is not None:
                    dex_ratio_vals.append(dex_ratio)
                    if dex_ratio >= 0.5:
                        direct_dex_cnt += 1
                if hop2_ratio is not None:
                    hop2_ratio_vals.append(hop2_ratio)
                    if hop2_ratio >= 0.5:
                        hop2_dex_cnt += 1
                if gmgn_verified is not None and gmgn_verified >= 1:
                    gmgn_pass_cnt += 1
                if gmgn_verified == 2:
                    gmgn_double_cnt += 1
                acc_rows_data.append((
                    row[3],  # wallet_address
                    round(row[5] or 0, 4),  # hold_percentage
                    round(row[7] or 0, 2),  # buy_amt_usd
                    round(row[8] or 0, 2),  # sell_amt_usd
                    row[14] or '',  # entity_id
                    row[15] or 0,   # inbound_addresses
                    row[16] or 0,   # outbound_addresses
                    dex_ratio,
                    swap_in_value,
                    hop2_ratio,
                    gmgn_verified,
                ))

        latest_acc_hold = round(latest_acc_hold, 2)
        latest_avg_acc = round(sum(latest_avg_scores) / len(latest_avg_scores), 2) if latest_avg_scores else 0
        latest_only_buy_pct = round(only_buy_cnt / latest_acc_h * 100, 1) if latest_acc_h else 0
        latest_acc_pct_real = round(latest_acc_h / latest_real_users * 100, 1) if latest_real_users else 0
        acc_top100_pct = round(acc_top100 / latest_acc_h * 100, 1) if latest_acc_h else 0
        acc_rest_pct = round(acc_rest / latest_acc_h * 100, 1) if latest_acc_h else 0
        direct_dex_acc_pct = round(direct_dex_cnt / latest_acc_h * 100, 1) if latest_acc_h else 0
        hop2_dex_acc_pct = round(hop2_dex_cnt / latest_acc_h * 100, 1) if latest_acc_h else 0
        gmgn_pass_acc_pct = round(gmgn_pass_cnt / latest_acc_h * 100, 1) if latest_acc_h else 0
        gmgn_double_acc_pct = round(gmgn_double_cnt / latest_acc_h * 100, 1) if latest_acc_h else 0

        # ── 集中度 (Top1/3/5) ──
        acc_holds_sorted = sorted(
            [d[1] for d in acc_rows_data],
            reverse=True
        )
        top1_acc_hold = round(acc_holds_sorted[0], 2) if acc_holds_sorted else 0
        top3_acc_hold = round(sum(acc_holds_sorted[:3]), 2) if acc_holds_sorted else 0
        top5_acc_hold = round(sum(acc_holds_sorted[:5]), 2) if acc_holds_sorted else 0

        # ── 聚类检测: entity_id 优先, 回退到特征聚类 ──
        entity_groups: dict[str, list] = defaultdict(list)
        no_entity_rows = []
        for d in acc_rows_data:
            eid = d[4]
            if eid:
                entity_groups[eid].append(d)
            else:
                no_entity_rows.append(d)

        # 特征聚类 (对无 entity_id 的地址)
        feature_groups: dict[tuple, list] = defaultdict(list)
        for d in no_entity_rows:
            fkey = (d[1], d[2], d[3])  # hold%, buy, sell
            feature_groups[fkey].append(d)

        # 计算加权有效吸筹数和只买不卖数
        eff_acc_h = 0.0
        eff_only_buy = 0.0
        cluster_wallets = 0

        # entity_id 组
        for eid, group in entity_groups.items():
            size = len(group)
            w = math.sqrt(size) if size >= 3 else float(size)
            eff_acc_h += w
            ob = sum(1 for d in group if d[3] == 0 and d[2] > 0)
            eff_only_buy += w * (ob / size) if size > 0 else 0
            if size >= 3:
                cluster_wallets += size

        # 特征聚类组
        for fkey, group in feature_groups.items():
            size = len(group)
            w = math.sqrt(size) if size >= 3 else float(size)
            eff_acc_h += w
            ob = sum(1 for d in group if d[3] == 0 and d[2] > 0)
            eff_only_buy += w * (ob / size) if size > 0 else 0
            if size >= 3:
                cluster_wallets += size

        cluster_ratio = round(cluster_wallets / latest_acc_h * 100, 2) if latest_acc_h else 0

        # ── 跨快照活跃度 (d8 数据) ──
        activity = None
        if prev_rows:
            prev_acc_wallets = set()
            prev_acc_hold = 0.0
            prev_acc_h = 0
            for row in prev_rows:
                if row[9] == 1:  # is_accumulating
                    prev_acc_wallets.add(row[3])  # wallet_address
                    prev_acc_hold += row[5] or 0
                    prev_acc_h += 1
            prev_acc_hold = round(prev_acc_hold, 2)

            current_acc_wallets = set(d[0] for d in acc_rows_data)
            new_wallets = current_acc_wallets - prev_acc_wallets
            lost_wallets = prev_acc_wallets - current_acc_wallets

            delta_acc_hold = latest_acc_hold - prev_acc_hold
            new_acc_rate = len(new_wallets) / prev_acc_h if prev_acc_h > 0 else 0

            activity = {
                'delta_acc_hold': round(delta_acc_hold, 2),
                'new_acc_count': len(new_wallets),
                'lost_acc_count': len(lost_wallets),
                'new_acc_rate': round(new_acc_rate, 4),
                'prev_acc_h': prev_acc_h,
            }

        latest_structure[(chain, addr)] = {
            "latest_snapshot": latest_rows[0][2] if latest_rows else '',
            "latest_total_h": latest_total_h,
            "latest_real_users": latest_real_users,
            "latest_acc_h": latest_acc_h,
            "latest_acc_pct_real": latest_acc_pct_real,
            "latest_acc_hold": latest_acc_hold,
            "latest_avg_acc": latest_avg_acc,
            "latest_only_buy_pct": latest_only_buy_pct,
            "top1_acc_hold": top1_acc_hold,
            "top3_acc_hold": top3_acc_hold,
            "top5_acc_hold": top5_acc_hold,
            "acc_top100_pct": acc_top100_pct,
            "acc_rest_pct": acc_rest_pct,
            # v4 新增
            "cluster_ratio": cluster_ratio,
            "eff_acc_h": round(eff_acc_h, 2),
            "eff_only_buy": round(eff_only_buy, 2),
            "direct_dex_acc_pct": direct_dex_acc_pct,
            "hop2_dex_acc_pct": hop2_dex_acc_pct,
            "gmgn_pass_acc_pct": gmgn_pass_acc_pct,
            "gmgn_double_acc_pct": gmgn_double_acc_pct,
            "avg_dex_ratio_acc": round(sum(dex_ratio_vals) / len(dex_ratio_vals), 4) if dex_ratio_vals else 0,
            "avg_hop2_ratio_acc": round(sum(hop2_ratio_vals) / len(hop2_ratio_vals), 4) if hop2_ratio_vals else 0,
            "activity": activity,
        }
    return latest_structure


def classify_review(result: dict) -> tuple[str, list[str]]:
    """按方法论文档中的人工复核框架生成标签与优先级。"""
    tags = []

    if result["snap_count"] == 1:
        tags.append("单快照待确认")

    if result["name"] == "未知" or result["symbol"] == "?":
        tags.append("名称/符号缺失")

    if result["sell_buy_ratio"] >= 50:
        tags.append("卖压偏高")
    elif result["sell_buy_ratio"] >= 30:
        tags.append("卖压较高")

    if result["latest_only_buy_pct"] >= 80:
        tags.append("当前买盘强")
    elif 0 < result["latest_only_buy_pct"] <= 40:
        tags.append("当前卖压需观察")

    if result.get("direct_dex_acc_pct", 0) >= 30:
        tags.append("直接DEX来源强")
    elif result.get("direct_dex_acc_pct", 0) >= 10:
        tags.append("直接DEX来源")

    if result.get("hop2_dex_acc_pct", 0) >= 15:
        tags.append("二跳DEX来源")
    if result.get("gmgn_pass_acc_pct", 0) >= 5:
        tags.append("GMGN验证通过")
    if result.get("gmgn_double_acc_pct", 0) >= 3:
        tags.append("GMGN双确认")

    if result["top1_acc_hold"] >= 50:
        tags.append("单一大户主导")
    elif result["top5_acc_hold"] >= 70:
        tags.append("高集中度")
    elif result["top1_acc_hold"] >= 30 or result["top5_acc_hold"] >= 60:
        tags.append("集中度偏高")
    elif result["latest_acc_h"] >= 100 and result["top5_acc_hold"] <= 20:
        tags.append("分散吸筹")
    elif (result["latest_acc_h"] >= 100 and
          result["top5_acc_hold"] <= 30 and
          result["top1_acc_hold"] <= 20):
        tags.append("结构良好")
    else:
        tags.append("结构中性")

    # ── Gecko 市场结构标签 ──
    gecko_tags = result.get("_gecko_tags", [])
    tags.extend(gecko_tags)

    if result["snap_count"] == 1:
        review_priority = "待确认"
    elif any(tag in tags for tag in {"单一大户主导", "高集中度", "卖压偏高",
                                      "市值虚高-高风险", "确认洗盘"}):
        review_priority = "谨慎复核"
    elif (result["level"] in {"S", "A"} and
          any(tag in tags for tag in {"分散吸筹", "结构良好"}) and
          result["sell_buy_ratio"] <= 25 and
          result["snap_count"] >= 3):
        review_priority = "优先复核"
    else:
        review_priority = "正常复核"

    return review_priority, tags

# ====================================================================
# MD 报告生成
# ====================================================================

def _load_last_stats() -> dict | None:
    """加载上次运行的统计数据用于对比。"""
    p = REPORT_DIR / "last_stats.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _save_last_stats(stats: dict, total: int, date_str: str) -> None:
    """保存本次统计数据供下次对比。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": date_str,
        "total": total,
        **stats,
    }
    (REPORT_DIR / "last_stats.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _fmt_delta(cur: int, prev: int | None) -> str:
    """格式化增减变化文本。"""
    if prev is None:
        return "-"
    d = cur - prev
    return f"+{d}" if d >= 0 else str(d)


def _fmt_mcap(mcap) -> str:
    if not mcap or mcap <= 0:
        return "N/A"
    if mcap >= 1e9:
        return f"${mcap/1e9:.1f}B"
    if mcap >= 1e6:
        return f"${mcap/1e6:.1f}M"
    if mcap >= 1e3:
        return f"${mcap/1e3:.0f}K"
    return f"${mcap:,.0f}"


def generate_md_report(
    results: list[dict],
    stats: dict,
    review_stats: dict,
    csv_path: Path,
    json_path: Path,
) -> Path:
    """生成 Markdown 分析报告，完全参照标准格式。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    date_short = now.strftime("%m-%d")
    time_str = now.strftime("%Y-%m-%d %H:%M")
    file_date = now.strftime("%Y%m%d")

    # 数据库大小
    try:
        db_size_mb = os.path.getsize(DB_PATH) / (1024 * 1024)
        db_size_str = f"{db_size_mb:.0f}MB"
    except Exception:
        db_size_str = "未知"

    total_tokens = len(results)
    last = _load_last_stats()
    last_date = last["date"] if last else None
    last_date_short = last_date[5:] if last_date else "上次"

    # ── 各等级统计 ──
    level_counts = Counter(r["level"] for r in results)
    # 等级平均综合分
    level_avg = {}
    for lv in ["S", "A", "B", "C", "D"]:
        scores = [r["composite"] for r in results if r["level"] == lv]
        level_avg[lv] = round(sum(scores) / len(scores), 1) if scores else 0

    # 快照深度统计
    snap_counts = Counter(r["snap_count"] for r in results)
    snap_sorted = sorted(snap_counts.items())

    # 链分布
    chain_all = Counter(r["chain"] for r in results)
    chain_s = Counter(r["chain"] for r in results if r["level"] == "S")

    # 复核统计 (全体 + S 级)
    s_review = Counter(r["review_priority"] for r in results if r["level"] == "S")

    # ── 筛选关键列表 ──
    priority_s = [r for r in results if r["level"] == "S" and r["review_priority"] == "优先复核"]
    caution_s = [r for r in results if r["level"] == "S" and r["review_priority"] == "谨慎复核"]
    priority_s.sort(key=lambda x: -x["composite"])
    caution_s.sort(key=lambda x: -x["composite"])

    # 持续优秀 (优先复核 + 快照 >= 7)
    sustained = [r for r in priority_s if r["snap_count"] >= 7]
    sustained.sort(key=lambda x: -x["snap_count"])

    lines: list[str] = []
    W = lines.append

    # ================================================================
    # 报告头部
    # ================================================================
    W(f"# 吸筹扫描分析报告 — {date_str}")
    W("")
    W(f"> 引擎版本: V4.2.2 八维评分 + DEX证据观测 | 数据库: select.db ({db_size_str}) | 生成时间: {time_str}")
    W("")
    W("---")
    W("")

    # ================================================================
    # §1 总览
    # ================================================================
    W("## 1. 总览")
    W("")
    if last:
        W(f"| 指标 | {last_date_short} | **{date_short}** | 变化 |")
        W("|------|-------|-----------|------|")
        W(f"| 代币总数 | {last.get('total', '-')} | **{total_tokens}** | {_fmt_delta(total_tokens, last.get('total'))} |")
        for lv, lname in [("S", "S 级"), ("A", "A 级"), ("B", "B 级"), ("C", "C 级"), ("D", "D 级")]:
            cur = level_counts.get(lv, 0)
            prev = last.get(lv)
            bold = "**" if lv in ("S", "A") else ""
            W(f"| {bold}{lname}{bold} | {prev if prev is not None else '-'} | {bold}{cur}{bold} | {_fmt_delta(cur, prev)} |")
    else:
        W("| 指标 | 数量 |")
        W("|------|------|")
        W(f"| 代币总数 | **{total_tokens}** |")
        for lv, lname in [("S", "S 级"), ("A", "A 级"), ("B", "B 级"), ("C", "C 级"), ("D", "D 级")]:
            W(f"| {lname} | {level_counts.get(lv, 0)} |")
    W("")

    # S 级暴增提示
    s_count = level_counts.get("S", 0)
    if last and last.get("S") and s_count > last["S"] * 2:
        ratio = round(s_count / last["S"], 1)
        chain_desc = ", ".join(f"{c}: {n}" for c, n in chain_all.most_common())
        W("> [!IMPORTANT]")
        W(f"> S 级从 {last['S']} → {s_count}，暴增 **{ratio} 倍**。"
          f"主因：链分布 ({chain_desc})，快照深度提升致趋势分拉满。")
        W("")

    # ── §1.1 等级均分 ──
    W("### 等级平均综合分")
    W("")
    W("| 等级 | 平均分 |")
    W("|------|--------|")
    for lv in ["S", "A", "B", "C", "D"]:
        if level_counts.get(lv, 0) > 0:
            W(f"| {lv} | {level_avg[lv]} |")
    W("")

    # ── §1.2 快照深度 ──
    W("### 快照深度")
    W("")
    W("| 快照次数 | 代币数 |")
    W("|----------|--------|")
    for snap, cnt in snap_sorted:
        W(f"| {snap} 次 | {cnt} |")
    W("")

    # ── §1.3 复核分布 ──
    W("### 复核优先级分布")
    W("")
    W("| 优先级 | 全体 | S 级 |")
    W("|--------|------|------|")
    for pri in ["优先复核", "正常复核", "谨慎复核", "待确认"]:
        all_cnt = review_stats.get(pri, 0)
        s_cnt = s_review.get(pri, 0)
        if all_cnt > 0:
            W(f"| {pri} | {all_cnt} | {s_cnt} |")
    W("")
    W("---")
    W("")

    # ================================================================
    # §2 链分布
    # ================================================================
    W("## 2. 链分布")
    W("")
    W("| 链 | 总数 | S 级 | S 占比 |")
    W("|----|------|------|--------|")
    for chain in sorted(chain_all, key=lambda c: -chain_all[c]):
        s_n = chain_s.get(chain, 0)
        pct = round(s_n / chain_all[chain] * 100, 1) if chain_all[chain] else 0
        W(f"| **{chain.upper()}** | {chain_all[chain]} | {s_n} | {pct}% |")
    W("")
    if s_count > 0:
        top_chain = chain_s.most_common(1)[0]
        top_pct = round(top_chain[1] / s_count * 100, 1)
        W(f"> {top_chain[0].upper()} 链占据 S 级的 **{top_pct}%**（{top_chain[1]}/{s_count}）。")
        W("")
    W("---")
    W("")

    # ================================================================
    # §3 优先复核 S 级 TOP 20
    # ================================================================
    W("## 3. 🏆 综合评分 TOP 20")
    W("")
    W("> 按八维综合分降序排列，展示最高质量标的")
    W("")

    top20 = results[:20]
    if top20:
        W("| # | 代币 | 链 | 等级 | 综合分 | 市值 | d8活跃 | DEX证据(直/跳/G) | 聚类% | 吸筹占比 | 均分 | 卖/买比 | 最新只买% | Top5% | 复核 | 标签 |")
        W("|---|------|-----|:----:|--------|------|:------:|:----------------:|:-----:|----------|------|---------|:---------:|:-----:|:----:|------|")
        for i, r in enumerate(top20, 1):
            name_str = f"**{r['name']} ({r['symbol']})** " if i <= 10 else f"{r['name']} ({r['symbol']})"
            tags_short = "·".join(t for t in r['structure_tags'] if t not in ('结构中性',))
            level_str = f"**{r['level']}**" if r['level'] == 'S' else r['level']
            review_short = r['review_priority'].replace('复核', '')
            mcap_str = _fmt_mcap(r.get('mcap'))
            dex_short = (
                f"{r['direct_dex_acc_pct']:.0f}/"
                f"{r['hop2_dex_acc_pct']:.0f}/"
                f"{r['gmgn_pass_acc_pct']:.0f}%"
            )
            W(f"| {i} | {name_str} | {r['chain']} | {level_str} | {r['composite']} | "
              f"{mcap_str} | {r['d8']} | {dex_short} | {r['cluster_ratio']:.1f} | "
              f"{r['acc_pct_real']}% | {r['avg_acc']} | {r['sell_buy_ratio']}% | "
              f"{r['latest_only_buy_pct']}% | {r['top5_acc_hold']}% | "
              f"{review_short} | {tags_short} |")
        W("")

        W("> [!TIP]")
        W("> **101-300 占比 > 60%** 说明吸筹广泛分布在中尾部，非少数大户独占。")
        W("> `DEX证据(直/跳/G)` = 直接 DEX 来源占比 / 二跳 DEX 来源占比 / GMGN 验证通过占比。")
        high_top100 = [r for r in top20 if r['acc_top100_pct'] > 45]
        if high_top100:
            names = "、".join(f"**{r['name']}** ({r['acc_top100_pct']}%)" for r in high_top100[:3])
            W(f"> {names} 头部占比高，大户参与更强。")
        W("")

        sustained_top20 = [r for r in top20 if r['snap_count'] >= 7]
        sustained_top20.sort(key=lambda x: -x['snap_count'])
        if sustained_top20:
            W("### 🔄 持续优秀（高快照 TOP 20 内）")
            W("")
            W("| 代币 | 快照 | Top100 | 101-300 | 特征 |")
            W("|------|:----:|:------:|:-------:|------|")
            for r in sustained_top20[:6]:
                tags_short = "·".join(t for t in r['structure_tags'] if t not in ('结构中性',))
                W(f"| {r['name']} ({r['symbol']}) | **{r['snap_count']}** | "
                  f"{r['acc_top100_pct']}% | {r['acc_rest_pct']}% | {tags_short} |")
            W("")

        # ── TOP 20 亮点 ──
        W("### 🔑 TOP 20 亮点")
        W("")
        # 主流蓝筹
        mainstream = [r for r in top20 if r['name'] in (
            'ChainLink', 'Worldcoin', 'Uniswap', 'Lido DAO', 'Immutable X',
            'Memecoin', 'Aave', 'Maker', 'Arbitrum', 'Optimism',
            'ChainLink Token', 'ApeCoin', 'Decentraland MANA', 'ENA',
        )]
        if mainstream:
            names = "、".join(f"**{r['name']}**" for r in mainstream)
            W(f"- {names} 等主流蓝筹进入 TOP 20，大户持续吸筹被确认")

        # 买盘极强
        strong_buy = [r for r in top20 if r['latest_only_buy_pct'] >= 85 and r['top5_acc_hold'] < 10]
        if strong_buy:
            names = "、".join(f"**{r['name']}**" for r in strong_buy[:4])
            W(f"- {names} 买盘极强（只买不卖 > 85%），Top5 集中度 < 10%，典型分散吸筹")

        # 最高快照
        max_snap = max(top20, key=lambda x: x['snap_count'])
        if max_snap['snap_count'] >= 8:
            W(f"- **{max_snap['name']}** 达到 {max_snap['snap_count']} 次快照，趋势确认度最高")

        # 极低卖压
        low_sell = [r for r in top20 if r['sell_buy_ratio'] <= 3]
        if low_sell:
            for r in low_sell[:2]:
                W(f"- **{r['name']}** 卖/买比仅 {r['sell_buy_ratio']}%，几乎零卖出")
        W("")
    else:
        W("*本次无符合条件的代币*")
        W("")

    W("---")
    W("")

    # ================================================================
    # §4 谨慎复核 S 级 TOP 15
    # ================================================================
    W("## 4. ⚠️ 谨慎复核 S 级 TOP 15（高分但有风险）")
    W("")

    top15_c = caution_s[:15]
    if top15_c:
        W("| # | 代币 | 链 | 综合分 | 卖/买比 | Top5% | 风险标签 |")
        W("|---|------|-----|--------|---------|-------|----------|")
        for i, r in enumerate(top15_c, 1):
            sell_str = f"**{r['sell_buy_ratio']}%**" if r['sell_buy_ratio'] >= 50 else f"{r['sell_buy_ratio']}%"
            top5_str = f"**{r['top5_acc_hold']}%**" if r['top5_acc_hold'] >= 40 else f"{r['top5_acc_hold']}%"
            # 风险标签
            risks = []
            if r['sell_buy_ratio'] >= 50:
                risks.append("卖压偏高")
            if r['top5_acc_hold'] >= 60:
                risks.append("高集中度")
            elif r['top5_acc_hold'] >= 40:
                risks.append("集中度偏高")
            elif r['top1_acc_hold'] >= 50:
                risks.append("单一大户主导")
            if not risks:
                risks = [t for t in r['structure_tags'] if '卖压' in t or '集中' in t or '大户' in t]
            risk_str = "+".join(risks) if risks else "需关注"
            W(f"| {i} | {r['name']} ({r['symbol']}) | {r['chain']} | {r['composite']} | "
              f"{sell_str} | {top5_str} | {risk_str} |")
        W("")

        # 高集中度警告
        high_conc = [r for r in top15_c if r['top5_acc_hold'] >= 50]
        if high_conc:
            names = "、".join(f"**{r['name']}** (Top5={r['top5_acc_hold']}%)" for r in high_conc[:4])
            W("> [!WARNING]")
            W(f"> {names} 虽然综合分高，但持仓高度集中，结构脆弱风险大。")
            W("")
    else:
        W("*本次无符合条件的谨慎复核 S 级代币*")
        W("")

    W("---")
    W("")

    # ================================================================
    # §5 与上次关键变化分析
    # ================================================================
    if last:
        W("## 5. 与上次关键变化分析")
        W("")
        W("### 5.1 数据规模变化")
        W(f"- 代币: {last.get('total', '?')} → **{total_tokens}** ({_fmt_delta(total_tokens, last.get('total'))})")
        W(f"- 数据库: {db_size_str}")
        min_snap = min(snap_counts.keys()) if snap_counts else 0
        W(f"- 快照深度: 最少 {min_snap} 次")
        W("")

        W("### 5.2 等级变化")
        for lv in ["S", "A", "B", "C", "D"]:
            cur = level_counts.get(lv, 0)
            prev = last.get(lv)
            if prev is not None and prev != cur:
                W(f"- {lv} 级: {prev} → **{cur}** ({_fmt_delta(cur, prev)})")
        W("")
        W("---")
        W("")

    # ================================================================
    # §6 优化建议
    # ================================================================
    section_num = 6 if last else 5
    W(f"## {section_num}. 🔧 方法论观察与优化建议")
    W("")
    W("> [!NOTE]")
    W("> 以下发现来自本次扫描结果的深度分析。")
    W("")

    suggestions = []

    # d5 趋势膨胀检测
    d5_100_cnt = sum(1 for r in results if r['d5'] >= 99)
    d5_100_pct = round(d5_100_cnt / total_tokens * 100, 1) if total_tokens else 0
    if d5_100_pct > 60:
        suggestions.append(
            ("d5 趋势分膨胀",
             f"{d5_100_cnt}/{total_tokens} ({d5_100_pct}%) 代币 d5≈100，"
             f"区分力丧失。建议提高满分门槛或引入时间衰减")
        )

    # S 级数量过多
    if s_count > 100:
        suggestions.append(
            ("S 级代币过多",
             f"当前 S≥{LEVEL_S}，共 {s_count} 个达标。建议上调门槛至 80+")
        )

    # 集中度已在 v3.2 纳入 d7 维度，不再作为建议

    # market_cap 检测
    mcap_missing = sum(1 for r in results if not r['mcap'] or r['mcap'] <= 0)
    if mcap_missing > total_tokens * 0.5:
        suggestions.append(
            ("market_cap 缺失",
             f"{mcap_missing}/{total_tokens} 代币市值数据缺失，建议回填 token_names 元数据")
        )

    if suggestions:
        W("| 问题 | 建议 |")
        W("|------|------|")
        for issue, suggestion in suggestions:
            W(f"| {issue} | {suggestion} |")
        W("")

    W("---")
    W("")

    # ================================================================
    # §8 Gecko 市场结构异常
    # ================================================================
    section_num2 = section_num + 1
    gecko_rows = []
    try:
        _gecko_conn = sqlite3.connect(DB_PATH)
        _gecko_conn.row_factory = sqlite3.Row
        _gecko_rows = _gecko_conn.execute("""
            SELECT g.* FROM gecko_market_data g
            INNER JOIN (
                SELECT chain, token_address, MAX(scan_time) as max_st
                FROM gecko_market_data
                GROUP BY chain, token_address
            ) latest ON g.chain=latest.chain
                     AND g.token_address=latest.token_address
                     AND g.scan_time=latest.max_st
        """).fetchall()
        gecko_rows = [dict(r) for r in _gecko_rows]
        _gecko_conn.close()
    except Exception:
        gecko_rows = []

    if gecko_rows:
        gecko_map = {r["token_address"]: r for r in gecko_rows}
        high_fdv = [r for r in results if gecko_map.get(r["token"].lower(), {}).get("mcap_liq_ratio", 0) > 200]
        wash_trade = [r for r in results if gecko_map.get(r["token"].lower(), {}).get("vl_ratio", 0) > 10]

        W(f"## {section_num2}. 🦎 市场结构异常 (GeckoTerminal)")
        W("")
        W(f"> 数据源: gecko_market_data 最新快照 | 覆盖 {len(gecko_rows)} 代币")
        W("")

        if high_fdv:
            W("### 市值虚高 (FDV/LP > 200)")
            W("")
            W("| # | 代币 | 等级 | comp | FDV($) | LP($) | FDV/LP | 风险 |")
            W("|---|------|------|------|--------|-------|--------|------|")
            high_fdv.sort(key=lambda r: gecko_map.get(r["token"].lower(), {}).get("mcap_liq_ratio", 0), reverse=True)
            for i, r in enumerate(high_fdv[:20], 1):
                g = gecko_map.get(r["token"].lower(), {})
                ml = g.get("mcap_liq_ratio", 0)
                risk = "高" if ml > 500 else "中"
                W(f"| {i} | {r['symbol']} | {r['level']} | {r['composite']:.1f} | "
                  f"{_fmt_mcap(g.get('fdv_usd', 0))} | {_fmt_mcap(g.get('reserve_usd', 0))} | "
                  f"{ml:.1f} | {risk} |")
            W("")
            high_cnt = sum(1 for r in high_fdv if gecko_map.get(r["token"].lower(), {}).get("mcap_liq_ratio", 0) > 500)
            mid_cnt = len(high_fdv) - high_cnt
            W(f"> 高风险 {high_cnt} 个 (FDV/LP>500) | 中风险 {mid_cnt} 个 (200<FDV/LP≤500)")
            W("")

        if wash_trade:
            W("### 疑似洗盘 (V/L > 10)")
            W("")
            W("| # | 代币 | 等级 | comp | V/L | buyTx% | LP($) | vol24h($) | 判定 |")
            W("|---|------|------|------|-----|--------|-------|-----------|------|")
            wash_trade.sort(key=lambda r: gecko_map.get(r["token"].lower(), {}).get("vl_ratio", 0), reverse=True)
            for i, r in enumerate(wash_trade[:20], 1):
                g = gecko_map.get(r["token"].lower(), {})
                vl = g.get("vl_ratio", 0)
                bt = g.get("buy_tx_pct", 50)
                verdict = "确认洗盘" if 45 <= bt <= 55 else "疑似"
                W(f"| {i} | {r['symbol']} | {r['level']} | {r['composite']:.1f} | "
                  f"{vl:.1f} | {bt:.1f}% | {_fmt_mcap(g.get('reserve_usd', 0))} | "
                  f"{_fmt_mcap(g.get('volume_24h', 0))} | {verdict} |")
            W("")

        if not high_fdv and not wash_trade:
            W("> 无异常代币")
            W("")
    else:
        section_num2 = section_num + 1
        W(f"## {section_num2}. 🦎 市场结构异常 (GeckoTerminal)")
        W("")
        W("> 暂无 Gecko 数据。需先运行 BubbleMap 定时任务触发 Gecko 链式采集。")
        W("")

    W("---")
    W("")

    # ================================================================
    # §9 导出文件
    # ================================================================
    section_num3 = section_num2 + 1
    W(f"## {section_num3}. 📁 导出文件")
    W("")
    W("| 文件 | 路径 |")
    W("|------|------|")
    csv_link = str(csv_path).replace("\\", "/")
    json_link = str(json_path).replace("\\", "/")
    W(f"| CSV | [{csv_path.name}](file:///{csv_link}) |")
    W(f"| JSON | [{json_path.name}](file:///{json_link}) |")
    W("")

    # ── 写入文件 ──
    run_dir = REPORT_DIR / now.strftime("%Y%m%d_%H%M")
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")

    # 保存统计数据供下次对比 (放在 report/ 根目录)
    _save_last_stats(stats, total_tokens, date_str)

    return report_path


# ====================================================================
# 主流程
# ====================================================================

def main():
    enable_windows_utf8()
    mode, backfill_mcap = parse_cli(sys.argv)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    total = cursor.execute("SELECT COUNT(*) FROM bubblemap_holders").fetchone()[0]
    print(f"总记录数: {total}")

    # ----------------------------------------------------------------
    # Step 0: CMC 市值回填（默认跳过，避免本地扫描被远程串行回填阻塞）
    # ----------------------------------------------------------------
    mcap_updated = 0
    if backfill_mcap:
        print("\n[Step 0] CMC 市值回填")
        mcap_updated = backfill_market_cap(conn)
        if mcap_updated:
            print(f"  已更新 {mcap_updated} 个代币市值")
    else:
        print("\n[Step 0] CMC 市值回填: 跳过（默认关闭；传 --backfill-mcap 可开启）")

    # ----------------------------------------------------------------
    # Step 1: 代币级聚合
    # ----------------------------------------------------------------
    all_tokens = fetch_token_aggregates(cursor)
    print(f"有吸筹信号的代币: {len(all_tokens)}")

    # ----------------------------------------------------------------
    # Step 2: 趋势计算
    # ----------------------------------------------------------------
    trend_data = fetch_trend_data(cursor)

    # ----------------------------------------------------------------
    # Step 3: 信号质量
    # ----------------------------------------------------------------
    sig_quality = fetch_signal_quality(cursor)

    # ----------------------------------------------------------------
    # Step 4: 最新快照结构 + 快照时间范围
    # ----------------------------------------------------------------
    latest_structure = fetch_latest_structure(cursor)
    snap_ranges = fetch_snapshot_ranges(cursor)

    # ----------------------------------------------------------------
    # Step 5: 综合评分 + 复核标签
    # ----------------------------------------------------------------
    results = []

    # ── 加载 Gecko 市场标签 ──
    _gecko_tag_map = {}  # {token_address_lower: [tag1, tag2, ...]}
    try:
        _gc = cursor.execute("""
            SELECT g.token_address, g.vl_ratio, g.mcap_liq_ratio, g.buy_tx_pct
            FROM gecko_market_data g
            INNER JOIN (
                SELECT chain, token_address, MAX(scan_time) as max_st
                FROM gecko_market_data
                GROUP BY chain, token_address
            ) latest ON g.chain=latest.chain
                     AND g.token_address=latest.token_address
                     AND g.scan_time=latest.max_st
        """).fetchall()
        for row in _gc:
            addr_l = row[0]
            vl, ml, bt = row[1] or 0, row[2] or 0, row[3] or 50
            t = []
            if ml > 500:
                t.append("市值虚高-高风险")
            elif ml > 200:
                t.append("市值虚高-中风险")
            if vl > 10:
                t.append("V/L异常-疑似洗盘")
                if 45 <= bt <= 55:
                    t.append("确认洗盘")
            _gecko_tag_map[addr_l] = t
        print(f"[Gecko] 加载 {len(_gecko_tag_map)} 个代币市场标签")
    except Exception as e:
        print(f"[Gecko] 无数据或表不存在: {e}")

    for tok in all_tokens:
        chain       = tok[0]
        addr        = tok[1]
        key         = (chain, addr)
        latest      = latest_structure.get(key, {})
        acc_h       = tok[8]
        acc_pct_r   = tok[9] or 0
        avg_acc     = tok[10] or 0
        acc_hold    = tok[11] or 0
        only_buy    = tok[15] or 0
        snap_count  = tok[17]
        cex_hold    = tok[18] or 0

        # v4: 聚类感知的 d1/d4
        cluster_ratio = latest.get('cluster_ratio', 0)
        if cluster_ratio >= 10 and latest.get('eff_acc_h', 0) > 0:
            # 使用去重后有效吸筹数计算 d1/d4
            eff_acc_h = latest['eff_acc_h']
            real_users = latest.get('latest_real_users', 0) or 1
            eff_acc_pct = eff_acc_h / real_users * 100
            d1 = score_acc_pct(eff_acc_pct)
            eff_only_buy = latest.get('eff_only_buy', 0)
            d4 = min(100, (eff_only_buy / eff_acc_h * 100) * 1.5) if eff_acc_h > 0 else 0
        else:
            d1 = score_acc_pct(acc_pct_r)
            d4 = score_sell_supp(only_buy, acc_h)

        d2 = score_avg_acc(avg_acc)
        d3 = score_hold_ctl(latest.get('latest_acc_hold', 0))
        d5 = score_trend(trend_data.get(key))
        d6 = score_sig_quality(sig_quality.get(key, 0))
        d7 = score_concentration(
            latest.get('top1_acc_hold', 0),
            latest.get('top5_acc_hold', 0),
            acc_h
        )
        d8 = score_realtime_activity(latest.get('activity'))

        composite = compute_composite(d1, d2, d3, d4, d5, d6, d7, d8)
        level, label = get_level(composite)

        sell_buy_ratio = round(tok[13] / tok[12] * 100, 1) if tok[12] and tok[12] > 0 else 0

        # 活跃度摘要
        activity = latest.get('activity')
        delta_hold = activity['delta_acc_hold'] if activity else 0
        new_acc_cnt = activity['new_acc_count'] if activity else 0

        result = {
            'chain': tok[0], 'addr': addr,
            'name': tok[2], 'symbol': tok[3], 'mcap': tok[4],
            'total_h': tok[5], 'real_users': tok[6], 'non_real': tok[7],
            'acc_h': acc_h, 'acc_pct_real': acc_pct_r, 'avg_acc': avg_acc,
            'acc_hold': acc_hold,
            'acc_buy': tok[12], 'acc_sell': tok[13], 'acc_net': tok[14],
            'only_buy': only_buy, 'sell_buy_ratio': sell_buy_ratio,
            'snapshot': tok[16], 'snap_count': snap_count, 'cex_hold': cex_hold,
            'latest_total_h': latest.get('latest_total_h', 0),
            'latest_real_users': latest.get('latest_real_users', 0),
            'latest_acc_h': latest.get('latest_acc_h', 0),
            'latest_acc_pct_real': latest.get('latest_acc_pct_real', 0),
            'latest_acc_hold': latest.get('latest_acc_hold', 0),
            'latest_avg_acc': latest.get('latest_avg_acc', 0),
            'latest_only_buy_pct': latest.get('latest_only_buy_pct', 0),
            'top1_acc_hold': latest.get('top1_acc_hold', 0),
            'top3_acc_hold': latest.get('top3_acc_hold', 0),
            'top5_acc_hold': latest.get('top5_acc_hold', 0),
            'acc_top100_pct': latest.get('acc_top100_pct', 0),
            'acc_rest_pct': latest.get('acc_rest_pct', 0),
            # v4 新增
            'cluster_ratio': cluster_ratio,
            'eff_acc_h': latest.get('eff_acc_h', 0),
            'delta_hold': delta_hold,
            'new_acc_cnt': new_acc_cnt,
            'direct_dex_acc_pct': latest.get('direct_dex_acc_pct', 0),
            'hop2_dex_acc_pct': latest.get('hop2_dex_acc_pct', 0),
            'gmgn_pass_acc_pct': latest.get('gmgn_pass_acc_pct', 0),
            'gmgn_double_acc_pct': latest.get('gmgn_double_acc_pct', 0),
            'd1': round(d1, 1), 'd2': round(d2, 1), 'd3': round(d3, 1),
            'd4': round(d4, 1), 'd5': round(d5, 1), 'd6': round(d6, 1),
            'd7': round(d7, 1), 'd8': round(d8, 1),
            'composite': round(composite, 1),
            'level': level, 'label': label,
            'trend': trend_data.get(key),
        }

        # 注入 Gecko 标签
        result['_gecko_tags'] = _gecko_tag_map.get(addr.lower(), [])

        review_priority, structure_tags = classify_review(result)
        result['review_priority'] = review_priority
        result['structure_tags'] = structure_tags
        results.append(result)

    # ----------------------------------------------------------------
    # Step 6: 排序输出
    # ----------------------------------------------------------------
    results.sort(key=lambda x: -x['composite'])

    lc = defaultdict(int)
    review_stats = defaultdict(int)
    for r in results:
        lc[r['level']] += 1
        review_stats[r['review_priority']] += 1

    stats = {
        "S": lc["S"],
        "A": lc["A"],
        "B": lc["B"],
        "C": lc["C"],
        "D": lc["D"],
    }
    csv_path, json_path = export_results(results, stats)

    # 生成 MD 报告
    report_path = generate_md_report(results, stats, review_stats, csv_path, json_path)

    # ----------------------------------------------------------------
    # Step 7: 评分入库 (token_scores 表)
    # ----------------------------------------------------------------
    scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ensure_scores_table(conn)
    saved_count = save_scores_to_db(conn, results, scan_time, snap_ranges)
    print(f"\n[Step 7] 评分入库: {saved_count} 条写入 token_scores 表 (scan_time={scan_time})")

    print(f"\n综合评分 = 占比({W_ACC_PCT:.0%}) + 均分({W_AVG_SCORE:.0%}) + "
          f"持仓({W_HOLD_CTL:.0%}) + 卖抑({W_SELL_SUPP:.0%}) + "
          f"趋势({W_TREND:.0%}) + 信号质量({W_SIG_QUAL:.0%}) + 集中度({W_CONC:.0%}) + 活跃度({W_REALTIME:.0%})")
    print(f"各级分布: S={lc['S']} | A={lc['A']} | B={lc['B']} | C={lc['C']} | D={lc['D']}")
    print("复核分布: "
          f"优先复核={review_stats['优先复核']} | "
          f"谨慎复核={review_stats['谨慎复核']} | "
          f"正常复核={review_stats['正常复核']} | "
          f"待确认={review_stats['待确认']}")
    print(f"总计: {len(results)} 个代币\n")
    print(f"导出 CSV: {csv_path}")
    print(f"导出 JSON: {json_path}")
    print(f"报告 MD:  {report_path}\n")

    if mode == 4:
        conn.close()
        print(f"当前模式: {MODE_LABELS[mode]}")
        print("\n分析完成!")
        return

    filtered_results = filter_results(results, mode)
    filtered_levels = {r['level'] for r in filtered_results}
    print(f"当前模式: {MODE_LABELS[mode]}")

    for lcode, lname in [('S', '极强吸筹'), ('A', '强吸筹'),
                          ('B', '中等吸筹'), ('C', '弱吸筹'), ('D', '微弱信号')]:
        if lcode not in filtered_levels:
            continue
        ltokens = [r for r in filtered_results if r['level'] == lcode]
        if not ltokens:
            continue

        print(f"{'=' * 70}")
        print(f"[{lcode}级] {lname} -- {len(ltokens)} 个代币")
        print(f"{'=' * 70}")

        for i, r in enumerate(ltokens, 1):
            mcap_str = f"${r['mcap']:,.0f}" if r['mcap'] and r['mcap'] > 0 else "N/A"

            trend_str = ""
            if r['trend']:
                td = r['trend']
                trend_str = f" | {td['snaps']}次快照 稳定:{td['stability']}"
                if td['dir'] > 0:
                    trend_str += " [增持]"
            else:
                trend_str = " | 1次快照"

            print(f"\n  #{i} [{lcode}] {r['name']} ({r['symbol']}) | 综合:{r['composite']}")
            print(f"     链: {r['chain']} | 合约: {r['addr']}")
            print(f"     市值: {mcap_str} | 持有者: {r['total_h']}"
                  f"(真实:{r['real_users']} 非真实:{r['non_real']})")
            print(f"     吸筹: {r['acc_h']}/{r['real_users']}真实用户 "
                  f"({r['acc_pct_real']}%) | 均分: {r['avg_acc']}")
            print(f"     持仓控制: {r['acc_hold']}% | "
                  f"只买不卖: {r['only_buy']}/{r['acc_h']} | "
                  f"卖/买比: {r['sell_buy_ratio']}%")
            print(f"     CEX/合约持仓: {r['cex_hold']}%")
            print(f"     最新结构: 吸筹 {r['latest_acc_h']}/{r['latest_real_users']}真实用户 "
                  f"({r['latest_acc_pct_real']}%) | 最新只买不卖: {r['latest_only_buy_pct']}%")
            print(f"     最新集中度: Top1={r['top1_acc_hold']}% "
                  f"Top3={r['top3_acc_hold']}% Top5={r['top5_acc_hold']}% | "
                  f"复核: {r['review_priority']}")
            print(f"     吸筹分布: Top100={r['acc_top100_pct']}% | "
                  f"101-300={r['acc_rest_pct']}%")
            print(f"     标签: {', '.join(r['structure_tags'])}")
            print(f"     评分: 占比={r['d1']} 均分={r['d2']} 持仓={r['d3']} "
                  f"卖抑={r['d4']} 趋势={r['d5']} 信号质量={r['d6']} 集中度={r['d7']} 活跃度={r['d8']}{trend_str}")
            print(f"     聚类: cluster_ratio={r['cluster_ratio']:.1f}% | "
                  f"Δhold={r['delta_hold']:+.2f}% | 新增吸筹={r['new_acc_cnt']}")
            print(f"     DEX证据: 直接={r['direct_dex_acc_pct']:.1f}% | "
                  f"二跳={r['hop2_dex_acc_pct']:.1f}% | "
                  f"GMGN通过={r['gmgn_pass_acc_pct']:.1f}% "
                  f"(双确认={r['gmgn_double_acc_pct']:.1f}%)")

    conn.close()
    print("\n分析完成!")


if __name__ == '__main__':
    main()
