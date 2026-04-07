# -*- coding: utf-8 -*-
"""
Codex AI SUM: 聚类去重与 DEX 证据审计脚本（独立于 accumulation_scan_v3.py）

目标:
1) 读取最新 accumulation_scan_v3 导出的 JSON 作为基线分数
2) 从 SQLite 最新快照重算“去重后”关键维度:
   - d1: 吸筹占比（entity_id 优先 + 特征聚类回退）
   - d3: 持仓控制度（与主引擎 v4.0 保持相同最新快照口径，仅做一致性校验）
   - d4: 只买不卖占比（按聚类加权）
3) 统计上游 DEX 证据观测:
   - direct_dex_acc_pct: 直接 DEX 池来源占比
   - hop2_dex_acc_pct: 二跳 DEX 来源占比
   - gmgn_pass_acc_pct: GMGN swap 验证通过占比
4) 输出对比分布与“当前新评分最高 Top20”到 report/ 目录
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DB_PATH = Path(r"C:\Users\Administrator\.gemini\antigravity\playground\select-coin\data\select.db")
ROOT = Path(__file__).resolve().parent
EXPORT_DIR = ROOT / "exports"
REPORT_DIR = ROOT / "report"

# 与主引擎 v4.0 对齐的权重与维度标尺
W_ACC_PCT = 0.20
W_AVG_SCORE = 0.15
W_HOLD_CTL = 0.14
W_SELL_SUPP = 0.12
W_TREND = 0.06
W_SIG_QUAL = 0.13
W_CONC = 0.10
W_REALTIME = 0.10

D1_SCALE = 1.25  # 80% 吸筹占比 = 满分
D3_SCALE = 2.5   # 40% 最新快照持仓 = 满分
D4_SCALE = 1.5   # 66.7% 只买不卖 = 满分

ASSOC_GT10_THRESHOLD = 10
ASSOC_GT10_BONUS_MASS = 2.0  # 实验模式: 关联数 > 10 的地址按 2 个单位质量计入 d1/d4 权重

# 分级阈值
LEVEL_S = 75
LEVEL_A = 60
LEVEL_B = 45
LEVEL_C = 30


@dataclass
class LatestRow:
    chain: str
    token_address: str
    wallet_address: str
    hold_percentage: float
    buy_amt_usd: float
    sell_amt_usd: float
    acc_signals: str
    is_accumulating: int
    is_cex: int
    is_dex: int
    is_contract: int
    is_supernode: int
    inbound_addresses: int
    outbound_addresses: int
    entity_id: str
    dex_ratio: float | None
    swap_in_value: float
    dex_ratio_hop2: float | None
    gmgn_verified: int | None


@dataclass(frozen=True)
class RunOptions:
    assoc_gt10_bonus: bool = False

    @property
    def report_name(self) -> str:
        return "codex-ai-sum-assoc-gt10-bonus.md" if self.assoc_gt10_bonus else "codex-ai-sum.md"

    @property
    def json_prefix(self) -> str:
        return "codex_ai_sum_assoc_gt10_bonus" if self.assoc_gt10_bonus else "codex_ai_sum"

    @property
    def run_label(self) -> str:
        return "Codex 关联数>10加权评估报告" if self.assoc_gt10_bonus else "Codex 聚类去重与DEX证据审计报告"


def parse_options(argv: list[str]) -> RunOptions:
    return RunOptions(
        assoc_gt10_bonus="--assoc-gt10-bonus" in argv[1:],
    )


def _to_float(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _level_of(score: float) -> str:
    if score >= LEVEL_S:
        return "S"
    if score >= LEVEL_A:
        return "A"
    if score >= LEVEL_B:
        return "B"
    if score >= LEVEL_C:
        return "C"
    return "D"


def _level_rank(level: str) -> int:
    return {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}.get(level, 0)


def _normalize_signals(s: str | None) -> str:
    if not s:
        return ""
    parts = [x.strip() for x in s.split(",") if x.strip()]
    parts.sort()
    return ",".join(parts)


def _association_count(row: LatestRow) -> int:
    return max(row.inbound_addresses, row.outbound_addresses)


def find_latest_baseline_json() -> Path:
    files = sorted(EXPORT_DIR.glob("accumulation_scan_v3_*.json"))
    if not files:
        raise FileNotFoundError(
            f"未找到基线文件: {EXPORT_DIR / 'accumulation_scan_v3_*.json'}。\n"
            "请先运行: python -X utf8 accumulation_scan_v3.py 4"
        )
    return files[-1]


def load_baseline(path: Path) -> dict[tuple[str, str], dict]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    rows = obj.get("results", [])
    out: dict[tuple[str, str], dict] = {}
    for r in rows:
        chain = str(r.get("chain", "")).strip()
        addr = str(r.get("addr", "")).strip()
        if not chain or not addr:
            continue
        out[(chain, addr)] = r
    return out


def fetch_latest_rows(conn: sqlite3.Connection) -> dict[tuple[str, str], list[LatestRow]]:
    cur = conn.cursor()
    cur.execute(
        """
        WITH latest_snapshot AS (
            SELECT chain, token_address, MAX(snapshot_time) AS latest_snapshot
            FROM bubblemap_holders
            GROUP BY chain, token_address
        )
        SELECT
            b.chain,
            b.token_address,
            b.wallet_address,
            b.hold_percentage,
            b.buy_amt_usd,
            b.sell_amt_usd,
            b.acc_signals,
            b.is_accumulating,
            b.is_cex,
            b.is_dex,
            b.is_contract,
            b.is_supernode,
            b.inbound_addresses,
            b.outbound_addresses,
            b.entity_id,
            b.dex_ratio,
            b.swap_in_value,
            b.dex_ratio_hop2,
            b.gmgn_verified
        FROM bubblemap_holders b
        JOIN latest_snapshot s
          ON b.chain = s.chain
         AND b.token_address = s.token_address
         AND b.snapshot_time = s.latest_snapshot
        ORDER BY b.chain, b.token_address
        """
    )
    rows_by_token: dict[tuple[str, str], list[LatestRow]] = defaultdict(list)
    for row in cur.fetchall():
        r = LatestRow(
            chain=row[0],
            token_address=row[1],
            wallet_address=row[2],
            hold_percentage=_to_float(row[3]),
            buy_amt_usd=_to_float(row[4]),
            sell_amt_usd=_to_float(row[5]),
            acc_signals=row[6] or "",
            is_accumulating=int(row[7] or 0),
            is_cex=int(row[8] or 0),
            is_dex=int(row[9] or 0),
            is_contract=int(row[10] or 0),
            is_supernode=int(row[11] or 0),
            inbound_addresses=int(row[12] or 0),
            outbound_addresses=int(row[13] or 0),
            entity_id=row[14] or "",
            dex_ratio=_to_float(row[15], default=None),
            swap_in_value=_to_float(row[16]),
            dex_ratio_hop2=_to_float(row[17], default=None),
            gmgn_verified=int(row[18]) if row[18] is not None else None,
        )
        rows_by_token[(r.chain, r.token_address)].append(r)
    return rows_by_token


def compute_adjusted_metrics(rows: list[LatestRow], options: RunOptions) -> dict[str, float]:
    real_users = sum(
        1
        for r in rows
        if r.is_cex == 0 and r.is_dex == 0 and r.is_contract == 0 and r.is_supernode == 0
    )
    acc_rows = [r for r in rows if r.is_accumulating == 1]
    raw_acc_h = len(acc_rows)

    if real_users <= 0 or raw_acc_h <= 0:
        return {
            "real_users": float(real_users),
            "raw_acc_h": float(raw_acc_h),
            "eff_acc_h": 0.0,
            "raw_acc_pct": 0.0,
            "adj_acc_pct": 0.0,
            "raw_only_buy_pct": 0.0,
            "adj_only_buy_pct": 0.0,
            "latest_acc_hold": 0.0,
            "cluster_groups_ge3": 0.0,
            "cluster_wallets": 0.0,
            "cluster_ratio": 0.0,
            "assoc_gt10_wallets": 0.0,
            "assoc_gt10_pct": 0.0,
            "direct_dex_acc_pct": 0.0,
            "hop2_dex_acc_pct": 0.0,
            "gmgn_pass_acc_pct": 0.0,
            "gmgn_double_acc_pct": 0.0,
            "d1_adj": 0.0,
            "d3_adj": 0.0,
            "d4_adj": 0.0,
        }

    entity_groups: dict[str, list[LatestRow]] = defaultdict(list)
    feature_groups: dict[tuple, list[LatestRow]] = defaultdict(list)
    for r in acc_rows:
        if r.entity_id:
            entity_groups[r.entity_id].append(r)
        else:
            key = (
                round(r.hold_percentage, 4),
                round(r.buy_amt_usd, 2),
                round(r.sell_amt_usd, 2),
                _normalize_signals(r.acc_signals),
                r.is_cex,
                r.is_dex,
                r.is_contract,
                r.is_supernode,
            )
            feature_groups[key].append(r)

    weighted_total = 0.0
    weighted_only_buy = 0.0
    assoc_gt10_wallets = 0
    cluster_groups_ge3 = 0
    cluster_wallets = 0

    def _apply_group(group: list[LatestRow]) -> None:
        nonlocal weighted_total, weighted_only_buy, assoc_gt10_wallets
        nonlocal cluster_groups_ge3, cluster_wallets
        size = len(group)
        assoc_hits = sum(1 for row in group if _association_count(row) > ASSOC_GT10_THRESHOLD)
        assoc_gt10_wallets += assoc_hits
        cluster_mass = float(size)
        if options.assoc_gt10_bonus and assoc_hits > 0:
            cluster_mass += assoc_hits * (ASSOC_GT10_BONUS_MASS - 1.0)
        w = math.sqrt(cluster_mass) if size >= 3 else float(size)
        weighted_total += w
        only_buy_hits = sum(1 for row in group if row.sell_amt_usd == 0 and row.buy_amt_usd > 0)
        weighted_only_buy += w * (only_buy_hits / size) if size > 0 else 0.0
        if size >= 3:
            cluster_groups_ge3 += 1
            cluster_wallets += size

    for group in entity_groups.values():
        _apply_group(group)
    for group in feature_groups.values():
        _apply_group(group)

    raw_only_buy_h = sum(1 for r in acc_rows if r.sell_amt_usd == 0 and r.buy_amt_usd > 0)
    # 主引擎在 latest_structure CTE 中将最新快照吸筹持仓保留到 2 位小数后再参与 d3。
    latest_acc_hold = round(sum(r.hold_percentage for r in acc_rows), 2)
    direct_dex_acc_h = sum(1 for r in acc_rows if r.dex_ratio is not None and r.dex_ratio >= 0.5)
    hop2_dex_acc_h = sum(1 for r in acc_rows if r.dex_ratio_hop2 is not None and r.dex_ratio_hop2 >= 0.5)
    gmgn_pass_acc_h = sum(1 for r in acc_rows if (r.gmgn_verified or 0) >= 1)
    gmgn_double_acc_h = sum(1 for r in acc_rows if r.gmgn_verified == 2)

    raw_acc_pct = raw_acc_h / real_users * 100
    adj_acc_pct = weighted_total / real_users * 100
    raw_only_buy_pct = raw_only_buy_h / raw_acc_h * 100
    adj_only_buy_pct = weighted_only_buy / weighted_total * 100 if weighted_total > 0 else 0

    cluster_ratio = cluster_wallets / raw_acc_h * 100 if raw_acc_h > 0 else 0
    assoc_gt10_pct = assoc_gt10_wallets / raw_acc_h * 100 if raw_acc_h > 0 else 0

    d1_adj = min(100.0, adj_acc_pct * D1_SCALE)
    d3_adj = min(100.0, latest_acc_hold * D3_SCALE)
    d4_adj = min(100.0, adj_only_buy_pct * D4_SCALE)

    return {
        "real_users": float(real_users),
        "raw_acc_h": float(raw_acc_h),
        "eff_acc_h": weighted_total,
        "raw_acc_pct": raw_acc_pct,
        "adj_acc_pct": adj_acc_pct,
        "raw_only_buy_pct": raw_only_buy_pct,
        "adj_only_buy_pct": adj_only_buy_pct,
        "latest_acc_hold": latest_acc_hold,
        "cluster_groups_ge3": float(cluster_groups_ge3),
        "cluster_wallets": float(cluster_wallets),
        "cluster_ratio": cluster_ratio,
        "assoc_gt10_wallets": float(assoc_gt10_wallets),
        "assoc_gt10_pct": assoc_gt10_pct,
        "direct_dex_acc_pct": direct_dex_acc_h / raw_acc_h * 100 if raw_acc_h > 0 else 0.0,
        "hop2_dex_acc_pct": hop2_dex_acc_h / raw_acc_h * 100 if raw_acc_h > 0 else 0.0,
        "gmgn_pass_acc_pct": gmgn_pass_acc_h / raw_acc_h * 100 if raw_acc_h > 0 else 0.0,
        "gmgn_double_acc_pct": gmgn_double_acc_h / raw_acc_h * 100 if raw_acc_h > 0 else 0.0,
        "d1_adj": d1_adj,
        "d3_adj": d3_adj,
        "d4_adj": d4_adj,
    }


def compare_scores(
    baseline: dict[tuple[str, str], dict],
    latest_rows_by_token: dict[tuple[str, str], list[LatestRow]],
    options: RunOptions,
) -> list[dict]:
    out: list[dict] = []
    for key, base in baseline.items():
        rows = latest_rows_by_token.get(key)
        if not rows:
            continue

        d2 = _to_float(base.get("d2"))
        d5 = _to_float(base.get("d5"))
        d6 = _to_float(base.get("d6"))
        d7 = _to_float(base.get("d7"))
        d8 = _to_float(base.get("d8"))
        old_score = _to_float(base.get("composite"))

        m = compute_adjusted_metrics(rows, options)
        new_score = (
            m["d1_adj"] * W_ACC_PCT
            + d2 * W_AVG_SCORE
            + m["d3_adj"] * W_HOLD_CTL
            + m["d4_adj"] * W_SELL_SUPP
            + d5 * W_TREND
            + d6 * W_SIG_QUAL
            + d7 * W_CONC
            + d8 * W_REALTIME
        )
        new_score = round(new_score, 2)
        old_level = str(base.get("level", ""))
        new_level = _level_of(new_score)

        out.append(
            {
                "chain": key[0],
                "addr": key[1],
                "name": base.get("name", "未知"),
                "symbol": base.get("symbol", "?"),
                "old_level": old_level,
                "new_level": new_level,
                "old_score": old_score,
                "new_score": new_score,
                "delta": round(new_score - old_score, 2),
                "d1_old": _to_float(base.get("d1")),
                "d3_old": _to_float(base.get("d3")),
                "d4_old": _to_float(base.get("d4")),
                "d1_new": round(m["d1_adj"], 2),
                "d3_new": round(m["d3_adj"], 2),
                "d4_new": round(m["d4_adj"], 2),
                "raw_acc_h": int(m["raw_acc_h"]),
                "eff_acc_h": round(m["eff_acc_h"], 2),
                "real_users": int(m["real_users"]),
                "raw_acc_pct": round(m["raw_acc_pct"], 2),
                "adj_acc_pct": round(m["adj_acc_pct"], 2),
                "raw_only_buy_pct": round(m["raw_only_buy_pct"], 2),
                "adj_only_buy_pct": round(m["adj_only_buy_pct"], 2),
                "cluster_groups_ge3": int(m["cluster_groups_ge3"]),
                "cluster_wallets": int(m["cluster_wallets"]),
                "cluster_ratio": round(m["cluster_ratio"], 2),
                "assoc_gt10_wallets": int(m["assoc_gt10_wallets"]),
                "assoc_gt10_pct": round(m["assoc_gt10_pct"], 2),
                "direct_dex_acc_pct": round(m["direct_dex_acc_pct"], 2),
                "hop2_dex_acc_pct": round(m["hop2_dex_acc_pct"], 2),
                "gmgn_pass_acc_pct": round(m["gmgn_pass_acc_pct"], 2),
                "gmgn_double_acc_pct": round(m["gmgn_double_acc_pct"], 2),
                "latest_acc_hold": round(m["latest_acc_hold"], 4),
                "snap_count": int(_to_float(base.get("snap_count"))),
            }
        )
    return out


def make_report(
    baseline_path: Path,
    comparisons: list[dict],
    options: RunOptions,
) -> Path:
    now = datetime.now()
    run_dir = REPORT_DIR / now.strftime("%Y%m%d_%H%M")
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / options.report_name

    old_counts = Counter(x["old_level"] for x in comparisons)
    new_counts = Counter(x["new_level"] for x in comparisons)
    deltas = [x["delta"] for x in comparisons]
    by_new_score = sorted(
        comparisons,
        key=lambda x: (x["new_score"], x["old_score"]),
        reverse=True,
    )
    by_drop = sorted(comparisons, key=lambda x: x["delta"])
    by_assoc = sorted(
        [x for x in comparisons if x["assoc_gt10_wallets"] > 0],
        key=lambda x: (x["assoc_gt10_wallets"], x["assoc_gt10_pct"], x["new_score"]),
        reverse=True,
    )

    downgrade = 0
    same = 0
    upgrade = 0
    for x in comparisons:
        d = _level_rank(x["new_level"]) - _level_rank(x["old_level"])
        if d < 0:
            downgrade += 1
        elif d == 0:
            same += 1
        else:
            upgrade += 1

    def pct(n: int) -> str:
        return f"{(n / len(comparisons) * 100):.1f}%" if comparisons else "0.0%"

    lines: list[str] = []
    w = lines.append
    w(f"# {options.run_label} — {now.strftime('%Y-%m-%d %H:%M')}")
    w("")
    w("> 本报告由 `codex-ai-sum.py` 生成，不修改 `accumulation_scan_v3.py`。")
    w("")
    w("## 1. 输入与口径")
    w("")
    w(f"- 基线文件: `{baseline_path}`")
    w(f"- 数据库: `{DB_PATH}`")
    if options.assoc_gt10_bonus:
        w("- 修正内容: `d1`/`d4` 继续使用去重权重，并对 `max(inbound_addresses, outbound_addresses) > 10` 的吸筹地址按 `2.0` 单位质量计权")
        w(f"- 实验阈值: `association_count > {ASSOC_GT10_THRESHOLD}`")
    else:
        w("- 修正内容: `d1`(去重后吸筹占比), `d4`(去重后只买不卖)，并优先使用 `entity_id` 去重")
    w("- 对齐内容: `d3` 仍使用主引擎 v4.0 的最新快照公式 (`latest_acc_hold * 2.5`)")
    w("- 保持不变: `d2`/`d5`/`d6`/`d7`/`d8`、权重与分级阈值")
    w("- 新增观测: `direct_dex_acc_pct` / `hop2_dex_acc_pct` / `gmgn_pass_acc_pct` 仅用于解释，不直接改分")
    w("")
    w("## 2. 分布变化")
    w("")
    w("| 等级 | 基线 | 修正后 | 变化 |")
    w("|---|---:|---:|---:|")
    for lv in ["S", "A", "B", "C", "D"]:
        old_n = old_counts.get(lv, 0)
        new_n = new_counts.get(lv, 0)
        delta_n = new_n - old_n
        w(f"| {lv} | {old_n} | {new_n} | {delta_n:+d} |")
    w("")
    w(f"- 降级: **{downgrade}** ({pct(downgrade)})")
    w(f"- 持平: **{same}** ({pct(same)})")
    w(f"- 升级: **{upgrade}** ({pct(upgrade)})")
    if deltas:
        w(
            f"- 分数变化: 均值 `{statistics.mean(deltas):.2f}` / 中位 `{statistics.median(deltas):.2f}` / 最小 `{min(deltas):.2f}`"
        )
    w("")
    if options.assoc_gt10_bonus:
        total_assoc_wallets = sum(x["assoc_gt10_wallets"] for x in comparisons)
        touched_tokens = len(by_assoc)
        w("## 3. 关联数 > 10 加权摘要")
        w("")
        w(f"- 受加权影响的钱包数: **{total_assoc_wallets}**")
        w(f"- 受加权影响的代币数: **{touched_tokens}**")
        w("")
        w("| # | 代币 | 链 | >10 关联钱包 | 占吸筹地址比 | 新等级 | 新分 |")
        w("|---:|---|---|---:|---:|---|---:|")
        for idx, x in enumerate(by_assoc[:15], 1):
            token = f"{x['name']} ({x['symbol']})"
            w(
                f"| {idx} | {token} | {x['chain']} | {x['assoc_gt10_wallets']} | "
                f"{x['assoc_gt10_pct']:.2f}% | {x['new_level']} | {x['new_score']:.2f} |"
            )
        w("")

    w("## 4. 当前新评分最高代币 (Top 20)")
    w("")
    w("| # | 代币 | 链 | 新等级 | 新分 | Δ分 | 原→新 | 聚类占比 | DEX证据(直/跳/G) | >10关联% | acc(原/效) |")
    w("|---:|---|---|---|---:|---:|---|---:|---:|---:|---:|")
    for idx, x in enumerate(by_new_score[:20], 1):
        token = f"{x['name']} ({x['symbol']})"
        acc_eff = f"{x['raw_acc_h']}/{x['eff_acc_h']}"
        w(
            f"| {idx} | {token} | {x['chain']} | {x['new_level']} | "
            f"{x['new_score']:.2f} | {x['delta']:+.2f} | {x['old_level']}→{x['new_level']} | "
            f"{x['cluster_ratio']:.1f}% | "
            f"{x['direct_dex_acc_pct']:.0f}/{x['hop2_dex_acc_pct']:.0f}/{x['gmgn_pass_acc_pct']:.0f}% | "
            f"{x['assoc_gt10_pct']:.2f}% | {acc_eff} |"
        )
    w("")

    w("## 5. 受影响最大的代币 (Top 20 降分)")
    w("")
    w("| # | 代币 | 链 | 原等级 | 新等级 | 原分 | 新分 | Δ分 | 聚类占比 | DEX证据(直/跳/G) | >10关联% | acc(原/效) | d1(原→新) | d3(原→新) | d4(原→新) |")
    w("|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|")
    for idx, x in enumerate(by_drop[:20], 1):
        token = f"{x['name']} ({x['symbol']})"
        acc_eff = f"{x['raw_acc_h']}/{x['eff_acc_h']}"
        w(
            f"| {idx} | {token} | {x['chain']} | {x['old_level']} | {x['new_level']} | "
            f"{x['old_score']:.2f} | {x['new_score']:.2f} | {x['delta']:.2f} | "
            f"{x['cluster_ratio']:.1f}% | "
            f"{x['direct_dex_acc_pct']:.0f}/{x['hop2_dex_acc_pct']:.0f}/{x['gmgn_pass_acc_pct']:.0f}% | "
            f"{x['assoc_gt10_pct']:.2f}% | {acc_eff} | "
            f"{x['d1_old']:.1f}→{x['d1_new']:.1f} | {x['d3_old']:.1f}→{x['d3_new']:.1f} | "
            f"{x['d4_old']:.1f}→{x['d4_new']:.1f} |"
        )
    w("")

    clo = next((x for x in comparisons if x["chain"] == "bsc" and x["symbol"] == "CLO"), None)
    if clo:
        w("## 6. 你刚才样本 (CLO) 的修正结果")
        w("")
        w(f"- 原等级/分数: **{clo['old_level']} / {clo['old_score']:.2f}**")
        w(f"- 新等级/分数: **{clo['new_level']} / {clo['new_score']:.2f}**")
        w(f"- 分数变化: **{clo['delta']:.2f}**")
        w(f"- 聚类地址占比: `{clo['cluster_ratio']:.2f}%` (`{clo['cluster_wallets']}` / `{clo['raw_acc_h']}`)")
        w(f"- `>10` 关联钱包占比: `{clo['assoc_gt10_pct']:.2f}%` (`{clo['assoc_gt10_wallets']}` / `{clo['raw_acc_h']}`)")
        w(f"- DEX证据: 直接 `{clo['direct_dex_acc_pct']:.2f}%` / 二跳 `{clo['hop2_dex_acc_pct']:.2f}%` / GMGN通过 `{clo['gmgn_pass_acc_pct']:.2f}%`")
        w(f"- 吸筹占比: `{clo['raw_acc_pct']:.2f}%` → `{clo['adj_acc_pct']:.2f}%`")
        w(f"- 只买不卖占比: `{clo['raw_only_buy_pct']:.2f}%` → `{clo['adj_only_buy_pct']:.2f}%`")
        w("")

    w("## 7. 结论")
    w("")
    w("- 当前版本已将 `d3` 与主引擎 v4.0 完全对齐，本次差异主要来自 `d1/d4` 的聚类去重。")
    if options.assoc_gt10_bonus:
        w(f"- 实验模式会对 `association_count > {ASSOC_GT10_THRESHOLD}` 的吸筹地址追加正向权重，因此更容易抬高少数高关联样本的 `d1/d4`。")
    w("- 若同构地址簇占比高，原始 `acc_holders` 和 `only_buy` 容易高估。")
    w("- DEX pool / hop2 / GMGN swap 证据已进入观测层，可帮助区分“直接 DEX 吸筹”与“中转后二跳吸筹”。")
    w("- 本脚本提供“先评估再落地”的并行验证，不影响你现有主流程。")
    w("- 可将该结果作为 `v4.0` 前置验证依据，再决定是否正式改评分引擎。")
    w("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def save_json(comparisons: list[dict], options: RunOptions) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = EXPORT_DIR / f"{options.json_prefix}_{stamp}.json"
    payload = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "db_path": str(DB_PATH),
            "rows": len(comparisons),
            "assoc_gt10_bonus": options.assoc_gt10_bonus,
            "assoc_gt10_threshold": ASSOC_GT10_THRESHOLD,
            "assoc_gt10_bonus_mass": ASSOC_GT10_BONUS_MASS,
        },
        "results": comparisons,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def main() -> None:
    options = parse_options(sys.argv)
    baseline_path = find_latest_baseline_json()
    baseline = load_baseline(baseline_path)
    if not baseline:
        raise SystemExit("基线 JSON 无有效结果，请先运行 accumulation_scan_v3.py")

    conn = sqlite3.connect(str(DB_PATH))
    latest_rows_by_token = fetch_latest_rows(conn)
    conn.close()

    comparisons = compare_scores(baseline, latest_rows_by_token, options)
    if not comparisons:
        raise SystemExit("未生成对比结果，请检查数据库与基线文件是否匹配。")

    report_path = make_report(baseline_path, comparisons, options)
    json_path = save_json(comparisons, options)

    old_counts = Counter(x["old_level"] for x in comparisons)
    new_counts = Counter(x["new_level"] for x in comparisons)
    if options.assoc_gt10_bonus:
        touched_tokens = sum(1 for x in comparisons if x["assoc_gt10_wallets"] > 0)
        total_assoc_wallets = sum(x["assoc_gt10_wallets"] for x in comparisons)
        print(f"模式: assoc-gt10-bonus (>{ASSOC_GT10_THRESHOLD} 按 {ASSOC_GT10_BONUS_MASS:.1f} 单位质量计权)")
        print(f"受影响钱包: {total_assoc_wallets} | 受影响代币: {touched_tokens}")
    print(f"基线: S={old_counts['S']} A={old_counts['A']} B={old_counts['B']} C={old_counts['C']} D={old_counts['D']}")
    print(f"修正: S={new_counts['S']} A={new_counts['A']} B={new_counts['B']} C={new_counts['C']} D={new_counts['D']}")
    top20 = sorted(
        comparisons,
        key=lambda x: (x["new_score"], x["old_score"]),
        reverse=True,
    )[:20]
    print("新评分最高 Top20:")
    for idx, x in enumerate(top20, 1):
        print(
            f"{idx:>2}. {x['name']} ({x['symbol']}) [{x['chain']}] "
            f"{x['new_level']} {x['new_score']:.2f} Δ{x['delta']:+.2f}"
        )
    print(f"导出 JSON: {json_path}")
    print(f"报告 MD:   {report_path}")


if __name__ == "__main__":
    main()
