"""
AI-SUM V5 — 时序对齐引擎
- 对每个代币的最近 N 个快照构建 SnapshotDiff 对象
- 计算地址名册换手、吸筹变化、持仓集中度变化、买卖行为变化
- 支持差分缓存以加速重复扫描
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

import config, db_loader


# ============================================================
# 核心数据结构
# ============================================================

@dataclass
class SnapshotDiff:
    """两个相邻快照之间的结构性差异。"""

    # 代币标识
    chain: str
    token_address: str
    token_symbol: str

    # 快照时间
    t_new: str
    t_old: str
    hours_gap: float               # 实际时间间隔（小时）
    gap_warning: bool = False      # True = 间隔 > MAX_HOURS_GAP

    # === 人员变化 ===
    roster_size_new: int = 0       # 新快照持有者总数
    roster_size_old: int = 0       # 旧快照持有者总数
    new_addresses: list = field(default_factory=list)     # 新进入 Top300 的地址
    dropped_addresses: list = field(default_factory=list) # 退出 Top300 的地址
    roster_turnover_pct: float = 0.0  # 换手率 = len(new_addr) / max(roster_size_old, 1)

    # === 吸筹变化 ===
    acc_count_new: int = 0         # 新快照吸筹地址数
    acc_count_old: int = 0         # 旧快照吸筹地址数
    delta_acc_count: int = 0       # 吸筹人数变化（正=增加）
    new_acc_addresses: list = field(default_factory=list)  # 新进入且 is_accumulating=1
    new_acc_count: int = 0
    new_acc_only_buy: int = 0      # 新acc地址中 sell=0 and buy>0 的数量
    new_acc_avg_score: float = 0.0 # 新增吸筹地址均分
    new_acc_hold_sum: float = 0.0  # 新增吸筹地址持仓合计 (%)

    # === 持仓变化 ===
    acc_hold_new: float = 0.0      # 新快照吸筹总持仓 (%)
    acc_hold_old: float = 0.0      # 旧快照吸筹总持仓 (%)
    delta_acc_hold: float = 0.0    # 持仓变化（百分点）
    historical_acc_hold_median: float = 0.0  # 历史快照持仓中位数

    # === 买卖行为 ===
    latest_only_buy_pct: float = 0.0   # 最新快照 only_buy 比例
    latest_acc_holders: int = 0        # 最新快照吸筹人数（有效计数）

    # === 集中度变化（新快照前5吸筹地址持仓） ===
    top1_acc_hold_new: float = 0.0
    top5_acc_hold_new: float = 0.0

    # === V8.2 核心指标 ===
    institutional_hold_v8: float = 0.0   # 机构控盘率(%) — 100进制
    hidden_whale_count: int = 0          # 隐庄数量
    dex_verified_pct: float = 0.0        # DEX真金率(%) — 100进制


@dataclass
class TokenTimeSeries:
    """一个代币的完整时序分析结果（包含所有相邻快照 diff）."""
    chain: str
    token_address: str
    token_symbol: str
    snap_count: int
    latest_snapshot: str
    diffs: list[SnapshotDiff] = field(default_factory=list)

    @property
    def latest_diff(self) -> Optional[SnapshotDiff]:
        """最近一次快照差分（最关键）。"""
        return self.diffs[-1] if self.diffs else None

    @property
    def prev_diff(self) -> Optional[SnapshotDiff]:
        """前一次快照差分（用于趋势对比）。"""
        return self.diffs[-2] if len(self.diffs) >= 2 else None


# ============================================================
# 工具函数
# ============================================================

def _parse_time(t: str) -> datetime:
    """将快照时间字符串解析为 datetime（尝试多种格式）。"""
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # fallback ISO
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


def _hours_between(t_old: str, t_new: str) -> float:
    """计算两个快照时间字符串之间的小时数（新 - 旧，正数）。"""
    try:
        dt_new = _parse_time(t_new)
        dt_old = _parse_time(t_old)
        return max(0.0, (dt_new - dt_old).total_seconds() / 3600)
    except Exception:
        return 0.0


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    return statistics.median(values)


# ============================================================
# 核心计算
# ============================================================

def compute_snapshot_diff(
    holders_new: list[dict],
    holders_old: list[dict],
    t_new: str,
    t_old: str,
    chain: str,
    token_address: str,
    token_symbol: str,
    historical_hold_series: list[tuple[str, float]],
) -> SnapshotDiff:
    """
    计算两个快照之间的 SnapshotDiff。
    
    参数：
        holders_new / holders_old: load_snapshot_detail 返回的列表
        historical_hold_series:    load_acc_hold_history 返回的 [(t, hold_pct)] 列表
    """
    hours_gap = _hours_between(t_old, t_new)
    gap_warning = hours_gap > config.MAX_HOURS_GAP

    # 索引：wallet_address → holder 数据
    new_idx = {h["wallet_address"]: h for h in holders_new}
    old_idx = {h["wallet_address"]: h for h in holders_old}

    new_set = set(new_idx.keys())
    old_set = set(old_idx.keys())

    entered = list(new_set - old_set)  # 新进入
    dropped = list(old_set - new_set)  # 退出

    roster_size_new = len(new_set)
    roster_size_old = len(old_set)
    turnover = len(entered) / max(roster_size_old, 1)

    # 吸筹统计
    acc_new = [h for h in holders_new if h["is_accumulating"] == 1]
    acc_old = [h for h in holders_old if h["is_accumulating"] == 1]
    acc_count_new = len(acc_new)
    acc_count_old = len(acc_old)

    # 新进入且吸筹
    new_acc = [new_idx[a] for a in entered if new_idx[a].get("is_accumulating") == 1]
    new_acc_only_buy = sum(
        1 for h in new_acc
        if (h.get("sell_amt_usd") or 0) == 0 and (h.get("buy_amt_usd") or 0) > 0
    )
    new_acc_scores = [h.get("acc_score") or 0.0 for h in new_acc]
    new_acc_avg_score = sum(new_acc_scores) / max(len(new_acc_scores), 1)
    new_acc_hold_sum = sum(h.get("hold_percentage") or 0.0 for h in new_acc)

    # 持仓合计
    acc_hold_new = sum(h.get("hold_percentage") or 0.0 for h in acc_new)
    acc_hold_old = sum(h.get("hold_percentage") or 0.0 for h in acc_old)

    # 历史中位数
    hist_vals = [v for _, v in historical_hold_series]
    hist_median = _median(hist_vals) if hist_vals else 0.0

    # 只买不卖（最新快照全量）
    only_buy_cnt = sum(
        1 for h in acc_new
        if (h.get("sell_amt_usd") or 0) == 0 and (h.get("buy_amt_usd") or 0) > 0
    )
    latest_only_buy_pct = only_buy_cnt / max(acc_count_new, 1)

    # 集中度（新快照吸筹地址按持仓降序）
    acc_new_sorted = sorted(acc_new, key=lambda h: h.get("hold_percentage") or 0.0, reverse=True)
    top1_hold = acc_new_sorted[0]["hold_percentage"] if acc_new_sorted else 0.0
    top5_hold = sum(h.get("hold_percentage") or 0.0 for h in acc_new_sorted[:5])

    # ── V8.2 计算 ──
    # hold_percentage 在 bubblemap_holders 中为 100 进制 (2.5 = 2.5%)
    inst_hold = 0.0
    hidden_whales = 0
    for h in holders_new:
        hp = h.get('hold_percentage') or 0.0
        is_cex = h.get('is_cex')
        is_contract = h.get('is_contract')
        is_supernode = h.get('is_supernode')
        is_infra = is_cex or is_contract or is_supernode
        # 机构控盘：基础设施地址 + 持仓>=配置阈值 的大户
        if is_infra or hp >= config.HIDDEN_WHALE_HOLD_THRESHOLD:
            inst_hold += hp
            # 隐庄：不是基础设施但持仓>=配置阈值
            if not is_infra and hp >= config.HIDDEN_WHALE_HOLD_THRESHOLD:
                hidden_whales += 1

    # ── V8.3 DEX 真金率（hop2 + entity 聚簇穿透）──
    # Step 1: 收集每个 entity_id 组的最高 dex 信号
    entity_dex_max = {}  # entity_id -> max(dex_ratio, dex_ratio_hop2)
    for h in holders_new:
        eid = h.get('entity_id') or ''
        if not eid:
            continue
        dr = h.get('dex_ratio') or 0.0
        dr2 = h.get('dex_ratio_hop2') or 0.0
        gv = h.get('gmgn_verified') or 0
        best = max(dr, dr2)
        if gv >= 1:
            best = max(best, 1.0)  # gmgn 验证视为满分
        entity_dex_max[eid] = max(entity_dex_max.get(eid, 0.0), best)

    # Step 2: 对吸筹地址判定 DEX 验证
    dex_verified_count = 0
    for h in holders_new:
        if h.get('is_accumulating') != 1:
            continue
        dr = h.get('dex_ratio') or 0.0
        dr2 = h.get('dex_ratio_hop2') or 0.0
        gv = h.get('gmgn_verified') or 0
        # 直接判定
        if dr >= 0.5 or dr2 >= 0.5 or gv >= 1:
            dex_verified_count += 1
            continue
        # entity 聚簇穿透：组内有通过的则共享
        eid = h.get('entity_id') or ''
        if eid and entity_dex_max.get(eid, 0.0) >= 0.5:
            dex_verified_count += 1

    dex_pct = (dex_verified_count / max(acc_count_new, 1)) * 100.0

    return SnapshotDiff(
        chain=chain,
        token_address=token_address,
        token_symbol=token_symbol,
        t_new=t_new,
        t_old=t_old,
        hours_gap=hours_gap,
        gap_warning=gap_warning,
        roster_size_new=roster_size_new,
        roster_size_old=roster_size_old,
        new_addresses=entered,
        dropped_addresses=dropped,
        roster_turnover_pct=round(turnover, 4),
        acc_count_new=acc_count_new,
        acc_count_old=acc_count_old,
        delta_acc_count=acc_count_new - acc_count_old,
        new_acc_addresses=[h["wallet_address"] for h in new_acc],
        new_acc_count=len(new_acc),
        new_acc_only_buy=new_acc_only_buy,
        new_acc_avg_score=round(new_acc_avg_score, 2),
        new_acc_hold_sum=round(new_acc_hold_sum, 4),
        acc_hold_new=round(acc_hold_new, 4),
        acc_hold_old=round(acc_hold_old, 4),
        delta_acc_hold=round(acc_hold_new - acc_hold_old, 4),
        historical_acc_hold_median=round(hist_median, 4),
        latest_only_buy_pct=round(latest_only_buy_pct, 4),
        latest_acc_holders=acc_count_new,
        top1_acc_hold_new=round(top1_hold, 4),
        top5_acc_hold_new=round(top5_hold, 4),
        institutional_hold_v8=round(inst_hold, 2),
        hidden_whale_count=hidden_whales,
        dex_verified_pct=round(dex_pct, 2),
    )


# ============================================================
# 批量计算（全库）
# ============================================================

def build_time_series(
    conn,
    token: dict,
    use_cache: bool = True,
) -> Optional[TokenTimeSeries]:
    """
    构建单个代币的 TokenTimeSeries。
    - 加载最近 DEFAULT_SNAP_WINDOW 个快照
    - 计算相邻快照 diff（支持缓存）
    """
    chain = token["chain"]
    addr  = token["token_address"]
    sym   = token.get("token_symbol", "?")

    snap_times = db_loader.load_snapshot_times(conn, chain, addr)
    if len(snap_times) < 2:
        return None

    recent = snap_times[-config.DEFAULT_SNAP_WINDOW:]
    hist = db_loader.load_acc_hold_history(conn, chain, addr)

    ts = TokenTimeSeries(
        chain=chain,
        token_address=addr,
        token_symbol=sym,
        snap_count=len(snap_times),
        latest_snapshot=snap_times[-1],
    )

    # 加载最近 N+1 个快照的详情（逐对计算相邻 diff）
    snap_details: dict[str, list[dict]] = {}
    for t in recent:
        snap_details[t] = db_loader.load_snapshot_detail(conn, chain, addr, t)

    for i in range(1, len(recent)):
        t_old = recent[i - 1]
        t_new = recent[i]

        # 尝试读缓存
        cached = None
        if use_cache:
            cached = db_loader.load_diff_cache(conn, chain, addr, t_new, t_old)

        if cached:
            # 反序列化缓存（简单 dict，不需要完整 dataclass）
            diff = SnapshotDiff(**{
                k: v for k, v in cached.items()
                if k in SnapshotDiff.__dataclass_fields__
            })
            # list 字段 JSON 存的是 list，直接用
        else:
            diff = compute_snapshot_diff(
                holders_new=snap_details[t_new],
                holders_old=snap_details[t_old],
                t_new=t_new,
                t_old=t_old,
                chain=chain,
                token_address=addr,
                token_symbol=sym,
                historical_hold_series=hist,
            )
            if use_cache:
                db_loader.save_diff_cache(
                    conn, chain, addr, t_new, t_old,
                    asdict(diff),
                )

        ts.diffs.append(diff)

    return ts


def batch_build_time_series(
    conn,
    tokens: list[dict],
    use_cache: bool = True,
    verbose: bool = False,
) -> list[TokenTimeSeries]:
    """
    批量构建所有代币的时序分析。
    返回有效 TokenTimeSeries 列表（至少 2 个快照）。
    """
    results = []
    total = len(tokens)
    for i, token in enumerate(tokens):
        if verbose and i % 50 == 0:
            print(f"  时序对齐 {i}/{total}...", end="\r")
        ts = build_time_series(conn, token, use_cache=use_cache)
        if ts is not None:
            results.append(ts)

    if verbose:
        print(f"  时序对齐完成 {len(results)}/{total} 代币有效            ")

    return results


# 导出用
from dataclasses import asdict
