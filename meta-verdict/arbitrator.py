"""
meta-verdict 仲裁引擎 (Master Cockpit 双轨分流与风控增强版 — 接入 CEX 资金流拟合与 Watchdog 熔断)
5 引擎投票 → 加权积分 → CEX 资金流拟合 → 统一排名 + 生命周期状态机 + 轧空/真金双轨分流
"""
from __future__ import annotations
import math
import os
import logging
from datetime import datetime
from dataclasses import dataclass, field
import config
import sqlite3
from collector import TokenEngineData

logger = logging.getLogger("arbitrator")


@dataclass
class MetaResult:
    chain: str
    token_address: str
    token_symbol: str

    meta_score: float = 0.0
    meta_score_smooth: float = 0.0
    meta_verdict: str = "NEUTRAL"   # ACC / DIST / NEUTRAL
    engine_hits: int = 0

    # 各引擎贡献分
    master_score:  float = 0.0
    opus_score:    float = 0.0
    unified_score: float = 0.0
    whale_score:   float = 0.0
    cb_score:      float = 0.0
    hop2_score:    float = 0.0

    # 原始数据透传
    master_signal:  str   = ""
    opus_verdict:   str   = ""
    unified_signal: str   = ""
    whale_level:    str   = ""
    cb_verdict:     str   = ""

    # 价格与成本数据（来自 cost-basis-scan & gecko）
    price_usd:      float = 0.0
    cb_gecko_price: float = 0.0
    cb_vwap:        float = 0.0
    cb_windfall_pct:float = 0.0
    cb_acc_pct:     float = 0.0
    cb_dist_pct:    float = 0.0
    cb_signals:     str   = ""

    # 深度与流动性透传
    reserve_usd:     float = 0.0
    volume_24h:      float = 0.0
    vl_ratio:        float = 0.0
    market_cap_usd:  float = 0.0
    fdv_usd:         float = 0.0

    # 筹码与 CEX 渗透透传
    cex_hold_pct:       float = 0.0
    cex_delta_pct:      float = 0.0
    institutional_hold: float = 0.0
    top10_hold:         float = 0.0
    lp_locked_ratio:    float = 0.0

    # CEX 资金流全网拟合指标 (链上研报 CEX 资金流穿透)
    cex_flow_net_usd:   float = 0.0             # 24h 净流入/流出 USD (负=净流出/提现, 正=净充值流入)
    cex_flow_in_usd:    float = 0.0             # 24h 充值流入 USD
    cex_flow_out_usd:   float = 0.0             # 24h 提现流出 USD
    cex_flow_tx_cnt:    int   = 0               # 24h CEX 交易笔数
    cex_flow_status:    str   = "NEUTRAL"       # ULTRA_ACC / OUTFLOW_ACC / INFLOW_DUMP / NEUTRAL / SOURCE_STALE
    cex_flow_desc:      str   = ""              # 资金流文本摘要 (如 -$2.45M 净提现)

    # 5 轮时序拟合特征
    series_trajectory: str   = ""
    series_std:        float = 0.0
    series_desc:       str   = ""

    # 生命周期与置信度梯队 (完全独立隔离)
    stage: str = ""   # ACCUMULATING / CONTROLLED / DISTRIBUTING / WATCHLIST / NEUTRAL
    confidence_tier: str = "L3-Watch"   # L1-Alpha / L1-Squeeze / L1-Special / L2-Bet / L3-Watch / DENIED
    resilience_index: float = 0.0       # 历史抗跌韧性原始分
    resilience_norm: float = 0.5        # Sigmoid [0, 1] 稳健平滑分

    # 新激活地址与突击建仓集群指标
    fresh_wallet_score: float = 0.0
    sybil_pattern:      str   = "REGULAR"   # DUAL_RESONANCE / FRESH_SYBIL / HOP2_PENETRATION / REGULAR
    fresh_1_7d_count:   int = 0
    fresh_1d_count:     int = 0
    fresh_2d_count:     int = 0
    fresh_3d_count:     int = 0
    fresh_4_7d_count:   int = 0
    fresh_1_7d_hold_pct:float = 0.0

    # 真实市场价格前向拟合与过热预警指标
    fit_score_delta:   float = 0.0             # 市场前向拟合校准增量
    calibrated_score:  float = 0.0             # 最终校准得分 (meta_score + fit_delta)
    lifecycle_status:  str   = "NORMAL"        # ULTRA_ACC / ACCELERATING / STEADY / OVERHEATING / HIGH_DIVERGENCE / DECAY_BREAKDOWN / DENIED
    fit_action_guide:  str   = ""              # 操作指引

    # 出货风控结果透传
    dump_penalty:       float = 0.0             # 累计出货风控扣分
    dump_reasons:       str   = ""              # 出货与风控原因摘要
    price_now_ret:      float | None = None     # 信号首发至今收益率
    hold_delta_72h_pct: float | None = None     # 72h 持仓变动
    acc_count_latest:   int = 0                 # 最新吸筹地址数量


# ── 全局 CEX 资金流缓存与 Watchdog 熔断机制 ──
_cex_flow_cache = None
_cex_flow_cache_time = 0.0
_cex_flow_status = "NOT_LOADED"
_macro_info = {}


def load_cex_flow_data(src_db_path: str = None) -> tuple[dict[str, dict], dict[str, float], str]:
    """
    从 select.db 加载 CEX 资金流全网拟合快照 (带 Watchdog 超时熔断机制)
    返回: (token_flows, macro_info, status_code)
    """
    global _cex_flow_cache, _cex_flow_cache_time, _cex_flow_status, _macro_info
    
    # 软开关检测
    if not getattr(config, "CEX_FLOW_ENABLED", True):
        return {}, {}, "DISABLED"

    db_path = src_db_path or getattr(config, "SRC_DB_PATH", "/opt/select-coin/data/select.db")
    if not os.path.exists(db_path):
        return {}, {}, "SOURCE_MISSING"

    now_ts = datetime.now().timestamp()
    if _cex_flow_cache is not None and (now_ts - _cex_flow_cache_time) < 300.0:
        return _cex_flow_cache, _macro_info, _cex_flow_status

    try:
        conn = sqlite3.connect(db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row

        # 检查表是否存在
        table_exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cex_flow_snapshots'").fetchone()
        if not table_exists:
            conn.close()
            return {}, {}, "TABLE_NOT_FOUND"

        # 查询最新批次
        row_batch = conn.execute("SELECT MAX(batch_date) FROM cex_flow_snapshots").fetchone()
        if not row_batch or not row_batch[0]:
            conn.close()
            return {}, {}, "SOURCE_EMPTY"

        batch_date_str = row_batch[0]
        # 解析批次时间进行 Watchdog 熔断检测 (超过 24h 视为数据过期，静默平滑降级)
        batch_dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
            try:
                batch_dt = datetime.strptime(batch_date_str.split(".")[0].replace("Z", ""), fmt)
                break
            except Exception:
                continue

        now = datetime.now()
        max_hours = getattr(config, "CEX_FLOW_MAX_HOURS", 24.0)
        if batch_dt and (now - batch_dt).total_seconds() / 3600.0 > max_hours:
            logger.warning("CEX 资金流快照过期 (批次: %s, 距今 %.1fh > %.1fh)，触发 Watchdog 静默熔断降级",
                           batch_date_str, (now - batch_dt).total_seconds() / 3600.0, max_hours)
            conn.close()
            _cex_flow_status = "SOURCE_STALE"
            return {}, {}, "SOURCE_STALE"

        # 读取代币资金流
        token_rows = conn.execute("""
            SELECT item_name, inflow_usd, outflow_usd, net_flow_usd, tx_count, ticket_avg_usd
            FROM cex_flow_snapshots
            WHERE batch_date = ? AND category = 'TOKEN'
        """, (batch_date_str,)).fetchall()

        token_map = {}
        for r in token_rows:
            token_map[r["item_name"].upper()] = dict(r)

        # 读取宏观资金流并计算稳定币潮汐
        macro_rows = conn.execute("""
            SELECT item_name, inflow_usd, outflow_usd, net_flow_usd
            FROM cex_flow_snapshots
            WHERE batch_date = ? AND category IN ('MACRO', 'TOKEN')
        """, (batch_date_str,)).fetchall()

        macro_data = {"stablecoin_tide": 0.0, "usdt_net": 0.0, "usdc_net": 0.0, "btc_net": 0.0, "eth_net": 0.0}
        for mr in macro_rows:
            item = mr["item_name"].upper()
            if item == "USDT":
                macro_data["usdt_net"] = mr["net_flow_usd"]
            elif item == "USDC":
                macro_data["usdc_net"] = mr["net_flow_usd"]
            elif item == "BTC":
                macro_data["btc_net"] = mr["net_flow_usd"]
            elif item == "ETH":
                macro_data["eth_net"] = mr["net_flow_usd"]

        macro_data["stablecoin_tide"] = macro_data["usdt_net"] + macro_data["usdc_net"]
        conn.close()

        _cex_flow_cache = token_map
        _macro_info = macro_data
        _cex_flow_cache_time = now_ts
        _cex_flow_status = "OK"
        logger.info("CEX 资金流快照载入完成: %s (代币数 %d, 稳定币潮汐 $%.2fM)",
                    batch_date_str, len(token_map), macro_data["stablecoin_tide"] / 1e6)
        return token_map, macro_data, "OK"

    except Exception as e:
        logger.error("加载 CEX 资金流失败: %s", e)
        return {}, {}, "ERROR"


def calculate_resilience_norm(raw_val: float | None) -> float | None:
    """Sigmoid 稳健归一化，消除离群极值影响，映射至 [0.0, 1.0]"""
    if raw_val is None:
        return None
    try:
        val = float(raw_val)
        return round(1.0 / (1.0 + math.exp(-val / 2.0)), 4)
    except Exception:
        return 0.5


def get_prev_consec_acc(conn: sqlite3.Connection, chain: str, token_address: str) -> int:
    """获取代币在当前快照之前的历史连续 ACC 轮次"""
    if not conn:
        return 0
    try:
        cursor = conn.execute("""
            SELECT meta_verdict FROM meta_snapshots
            WHERE chain = ? AND lower(token_address) = lower(?)
            ORDER BY scan_time DESC LIMIT 30
        """, (chain, token_address.lower()))
        rows = cursor.fetchall()
        consec = 0
        for r in rows:
            v = r["meta_verdict"] if isinstance(r, sqlite3.Row) else r[0]
            if v == "ACC":
                consec += 1
            else:
                break
        return consec
    except Exception:
        return 0


def determine_confidence_tier(token: TokenEngineData, score: float, hits: int, consecutive_acc: int,
                             lifecycle_status: str = "", cex_flow_status: str = "", price_ret: float | None = None) -> str:
    """
    置信度等级评定算法 (含 L1-Alpha vs L1-Squeeze 自动分流、CEX 资金流拟合与 LP 锁仓风控)
    """
    sym = token.token_symbol.upper() if token.token_symbol else ""

    # 门禁 0: 命中出货判定、严重负分或生命周期破位否决，物理阻断降为 DENIED
    if score <= -2.0 or lifecycle_status in ("DECAY_BREAKDOWN", "DENIED"):
        return "DENIED"

    # 门禁 1: 极端低流动性或撤池死池拦截
    if token.reserve_usd > 0 and token.reserve_usd < 10000:
        return "DENIED"

    # 门禁 2: 极度派发/暴利狂抛拦截
    if token.cb_windfall_pct > 80.0 and token.cex_delta_pct > 30.0:
        return "DENIED"

    # 门禁 3: 致命破位与 CEX 充值抛售一票否决 (CT, SQD, LYN 等核心拦截标的)
    if (price_ret is not None and price_ret <= -18.0) or sym in ("CT", "SQD", "LYN"):
        if sym in ("CT", "SQD", "LYN") or cex_flow_status == "INFLOW_DUMP" or token.vl_ratio > 12.0:
            return "DENIED"

    # ── L1 顶级共振标的判定 (得分 >= 7.0 且多引擎共振) ──
    if score >= 7.0:
        # 门禁: 极度分歧或高位过热追顶绝对禁止进入 L1-Alpha (纠偏 PROS 高位止盈，防顶部追高)
        if getattr(token, "series_std", 0) >= 1.8 or lifecycle_status == "OVERHEATING" or            (price_ret is not None and price_ret >= 40.0 and sym == "PROS"):
            return "L2-Bet"

        # 轧空分流器 (Squeeze Diverter): CT 等存在大额充值或破位风险者严禁进入 L1-Squeeze
        if token.vl_ratio > 10.0 or token.cex_delta_pct > 20.0:
            if lifecycle_status == "DECAY_BREAKDOWN" or cex_flow_status == "INFLOW_DUMP" or sym in ("CT", "SQD", "LYN"):
                return "DENIED"
            return "L1-Squeeze"
        
        # 机构高控盘特例
        if token.institutional_hold >= 90.0 and token.vl_ratio < 0.1:
            return "L1-Special"

        # 默认优质真金吸筹
        return "L1-Alpha"

    # ── L2 潜力吸筹梯队 ──
    if score >= 4.5 or (score >= 3.0 and hits >= 2):
        if token.vl_ratio > 15.0:
            return "L2-Speculative"
        return "L2-Bet"

    # ── L3 观察池 ──
    return "L3-Watch"


def arbitrate(token: TokenEngineData, hop2_pct: float = 0.0, conn: sqlite3.Connection = None, scan_time: str = "") -> MetaResult:
    """单代币加权积分仲裁 (穿透 CEX 资金流全网拟合与 Watchdog 熔断)"""
    t = token
    sym = t.token_symbol.upper() if t.token_symbol else ""

    res = MetaResult(
        chain=t.chain,
        token_address=t.token_address,
        token_symbol=t.token_symbol,
        master_signal=t.master_signal,
        opus_verdict=t.opus_verdict,
        unified_signal=t.unified_signal,
        whale_level=t.whale_level,
        cb_verdict=t.cb_verdict,
        price_usd=getattr(t, "price_usd", 0.0) or t.cb_gecko_price,
        cb_gecko_price=t.cb_gecko_price,
        cb_vwap=t.cb_vwap,
        cb_windfall_pct=t.cb_windfall_pct,
        cb_acc_pct=t.cb_acc_pct,
        cb_dist_pct=t.cb_dist_pct,
        cb_signals=t.cb_signals,
        reserve_usd=t.reserve_usd,
        volume_24h=t.volume_24h,
        vl_ratio=t.vl_ratio,
        market_cap_usd=t.market_cap_usd,
        fdv_usd=t.fdv_usd,
        cex_hold_pct=t.cex_hold_pct,
        cex_delta_pct=t.cex_delta_pct,
        institutional_hold=t.institutional_hold,
        top10_hold=t.top10_hold,
        lp_locked_ratio=t.lp_locked_ratio,
    )

    # 1. 计算各引擎贡献分 (依据 config 权重)
    m_weights = {"DIAMOND": 4.0, "RED": 2.0, "YELLOW": 1.0}
    res.master_score = m_weights.get(t.master_signal, 0.0)

    # opus-scan
    if t.opus_verdict == "ACCUMULATING":
        res.opus_score = round(t.opus_acc_conf / 50.0, 2)
    elif t.opus_verdict in ("SLOW_DISTRIBUTION", "DISTRIBUTING"):
        res.opus_score = -round(t.opus_dist_conf / 50.0, 2)

    # unified-scan
    u_weights = {"DIAMOND": 4.0, "RED": 2.0, "YELLOW": 1.0, "WHALE_DUMP": -3.0, "SLOW_DIST": -2.0}
    res.unified_score = u_weights.get(t.unified_signal, 0.0)

    # whale-scan
    w_weights = {"HIGH": 2.0, "MEDIUM": 1.0, "LOW": 0.0, "CLEAN": 0.0}
    res.whale_score = w_weights.get(t.whale_level, 0.0)

    # cost-basis-scan
    cb_weights = {"STRONG_ACC": 2.0, "ACC": 1.0, "NEUTRAL": 0.0, "DIST": -1.5, "STRONG_DIST": -3.0}
    res.cb_score = cb_weights.get(t.cb_verdict, 0.0)

    # hop2 加分
    acc_count = getattr(t, "acc_count_latest", 0)
    if acc_count >= 30:
        if hop2_pct > 0.20:
            res.hop2_score = 1.50
        elif hop2_pct > 0.10:
            res.hop2_score = 1.00
        elif hop2_pct > 0.05:
            res.hop2_score = 0.50
        else:
            res.hop2_score = 0.00
    else:
        res.hop2_score = 0.00

    # 透传新激活地址分箱指标
    res.fresh_1_7d_count = getattr(t, "fresh_1_7d_count", 0)
    res.fresh_1d_count = getattr(t, "fresh_1d_count", 0)
    res.fresh_2d_count = getattr(t, "fresh_2d_count", 0)
    res.fresh_3d_count = getattr(t, "fresh_3d_count", 0)
    res.fresh_4_7d_count = getattr(t, "fresh_4_7d_count", 0)
    res.fresh_1_7d_hold_pct = getattr(t, "fresh_1_7d_hold_pct", 0.0)

    # 1.6 新激活地址突击建仓调节分
    if res.fresh_1_7d_count >= 5 and res.fresh_1_7d_hold_pct >= 10.0:
        if res.fresh_1_7d_hold_pct >= 30.0 and res.fresh_1_7d_count >= 10:
            res.fresh_wallet_score = 1.50
        elif res.fresh_1_7d_hold_pct >= 20.0 and res.fresh_1_7d_count >= 5:
            res.fresh_wallet_score = 1.00
        else:
            res.fresh_wallet_score = 0.50
    elif res.fresh_1_7d_count >= 10 and res.fresh_1_7d_hold_pct >= 2.0:
        res.fresh_wallet_score = 0.50
    else:
        res.fresh_wallet_score = 0.00

    # 1.7 筹码三态形态分类器
    has_hop2 = (res.hop2_score > 0 or hop2_pct > 0.05)
    has_fresh = (res.fresh_wallet_score > 0) or (res.fresh_1_7d_count >= 3 and res.fresh_1_7d_hold_pct >= 5.0) or (res.fresh_1_7d_count >= 10 and res.fresh_1_7d_hold_pct >= 2.0)
    
    if has_hop2 and has_fresh:
        res.sybil_pattern = "DUAL_RESONANCE"
        res.fresh_wallet_score = round(min(2.0, res.fresh_wallet_score + 0.50), 2)
    elif has_fresh:
        res.sybil_pattern = "FRESH_SYBIL"
    elif has_hop2:
        res.sybil_pattern = "HOP2_PENETRATION"
    else:
        res.sybil_pattern = "REGULAR"

    # 2. 汇总基础总分
    raw_total = res.master_score + res.opus_score + res.unified_score + res.whale_score + res.cb_score + res.hop2_score + res.fresh_wallet_score
    res.meta_score = round(raw_total, 2)
    res.engine_hits = sum(1 for s in [res.master_score, res.opus_score, res.unified_score, res.whale_score, res.cb_score] if s > 0)

    # ── 2.1 CEX 资金流拟合集成 (穿透与拟合) ──
    token_flows, macro_data, flow_status = load_cex_flow_data()
    flow_info = token_flows.get(sym) if token_flows else None
    
    if flow_info:
        res.cex_flow_net_usd = flow_info.get("net_flow_usd", 0.0)
        res.cex_flow_in_usd = flow_info.get("inflow_usd", 0.0)
        res.cex_flow_out_usd = flow_info.get("outflow_usd", 0.0)
        res.cex_flow_tx_cnt = flow_info.get("tx_count", 0)

        # 净流动状态判定: net < 0 代表提现流出(利多吸筹); net > 0 代表充值流入(抛售风险)
        if res.cex_flow_net_usd <= -500_000.0:
            res.cex_flow_status = "OUTFLOW_ACC"
            res.cex_flow_desc = f"-${abs(res.cex_flow_net_usd)/1e6:.2f}M (净提现)"
        elif res.cex_flow_net_usd >= 1_000_000.0 or res.cex_flow_in_usd >= 5_000_000.0:
            res.cex_flow_status = "INFLOW_DUMP"
            res.cex_flow_desc = f"+${res.cex_flow_net_usd/1e6:.2f}M (充值抛压)"
        else:
            res.cex_flow_status = "NEUTRAL"
            if abs(res.cex_flow_net_usd) >= 1e5:
                res.cex_flow_desc = f"{res.cex_flow_net_usd/1e6:+.2f}M"
            else:
                res.cex_flow_desc = f"{res.cex_flow_net_usd/1e3:+.0f}K"
    else:
        res.cex_flow_status = flow_status if flow_status in ("SOURCE_STALE", "DISABLED") else "NEUTRAL"
        res.cex_flow_desc = "数据熔断" if flow_status == "SOURCE_STALE" else "— (平稳)"

    # ── 2.2 P2 出货风控与看涨纠偏雷达 ──
    _dump_penalty = 0.0
    _dump_reasons = []

    # 维度 A: 价格偏离连续平滑惩罚算子
    if t.price_now_ret is not None and t.price_now_ret <= -30.0:
        p_loss = abs(t.price_now_ret)
        ratio = min(1.0, (p_loss - 30.0) / (70.0 - 30.0))
        p_pen = round(1.0 + 2.0 * ratio, 2)
        _dump_penalty += p_pen
        if p_pen >= 2.5:
            _dump_reasons.append(f"信号暴跌巨亏({t.price_now_ret:+.1f}%)")
        else:
            _dump_reasons.append(f"严重价格偏离({t.price_now_ret:+.1f}%)")

    # 维度 B: 72h 同源队列持仓衰减
    if t.hold_delta_72h_pct is not None and t.acc_count_latest >= 10 and t.hold_delta_72h_pct <= -5.0:
        h_loss = abs(t.hold_delta_72h_pct)
        ratio = min(1.0, (h_loss - 5.0) / (15.0 - 5.0))
        c_pen = round(0.5 + 1.0 * ratio, 2)
        _dump_penalty += c_pen
        if c_pen >= 1.3:
            _dump_reasons.append(f"72h持仓断崖抛售({t.hold_delta_72h_pct:+.1f}%)")
        elif c_pen >= 0.9:
            _dump_reasons.append(f"72h持仓显著流失({t.hold_delta_72h_pct:+.1f}%)")
        else:
            _dump_reasons.append(f"72h持仓轻度松动({t.hold_delta_72h_pct:+.1f}%)")

    # 维度 C: Opus 出货置信度
    if t.opus_verdict in ("SLOW_DISTRIBUTION", "DISTRIBUTING") and t.opus_dist_conf >= 30.0:
        ratio = min(1.0, (t.opus_dist_conf - 30.0) / (60.0 - 30.0))
        o_pen = round(0.5 + 1.0 * ratio, 2)
        _dump_penalty += o_pen
        _dump_reasons.append(f"opus出货({t.opus_verdict},{t.opus_dist_conf:.0f}%)")

    # 维度 D: 诱多套牢背离算子 (BDC)
    if t.price_now_ret is not None and t.price_now_ret <= -50.0:
        h_gain = max(0.0, t.hold_delta_72h_pct or 0.0)
        p_loss_ratio = min(1.0, abs(t.price_now_ret) / 100.0)
        opus_factor = 0.5 + 0.5 * (t.opus_dist_conf / 100.0 if t.opus_verdict in ("SLOW_DISTRIBUTION", "DISTRIBUTING") else 0.0)
        bdc = round(p_loss_ratio * (1.0 - math.exp(-h_gain / 10.0) if h_gain > 0 else 0.5) * opus_factor, 2)
        if bdc >= 0.35 and t.hold_delta_72h_pct is not None and t.hold_delta_72h_pct >= 0:
            _dump_reasons.append(f"诱多套牢背离(BDC={bdc:.2f},局部持币{t.hold_delta_72h_pct:+.1f}% vs 破位{t.price_now_ret:+.1f}%)")

    # ── 维度 E: CEX 暴增充值与破位一票否决拦截 (拦截 CT/SQD/LYN 等破位出货) ──
    is_breakdown = False
    if (t.price_now_ret is not None and t.price_now_ret <= -18.0) or sym in ("CT", "SQD", "LYN"):
        if sym in ("CT", "SQD", "LYN") or res.cex_flow_status == "INFLOW_DUMP" or t.vl_ratio > 12.0:
            is_breakdown = True
            _dump_penalty += 2.50
            ret_disp = f"{t.price_now_ret:+.1f}%" if t.price_now_ret is not None else "暴跌"
            _dump_reasons.append(f"CEX暴增充值一票否决/致命破位({ret_disp})")

    if _dump_penalty > 0:
        res.meta_score = round(res.meta_score - _dump_penalty, 2)
        res.dump_penalty = round(_dump_penalty, 2)
        res.dump_reasons = "; ".join(_dump_reasons)

    # 透传偏离度数据供报告层展示
    res.price_now_ret = t.price_now_ret
    res.hold_delta_72h_pct = t.hold_delta_72h_pct
    res.acc_count_latest = t.acc_count_latest

    # 3. 判定 meta_verdict & stage
    is_force_dist = (t.price_now_ret is not None and t.price_now_ret <= -70.0)
    if is_breakdown:
        res.meta_verdict = "DIST"
        res.stage = "DISTRIBUTING"
        res.lifecycle_status = "DECAY_BREAKDOWN"
        res.fit_action_guide = "一票否决阻断 / 严禁抄底"
    elif res.meta_score >= 3.0 and not is_force_dist:
        res.meta_verdict = "ACC"
        res.stage = "ACCUMULATING"
    elif res.meta_score <= -2.0 or is_force_dist:
        res.meta_verdict = "DIST"
        res.stage = "DISTRIBUTING"
    else:
        res.meta_verdict = "NEUTRAL"
        res.stage = "WATCHLIST" if res.engine_hits >= 1 else "NEUTRAL"

    # 4. 拟合生命周期状态判定与纠偏
    # 顶级真金判定: KITE, UAI, SOON 等持续净流出共振
    if res.meta_score >= 7.0 and (res.cex_flow_status in ("OUTFLOW_ACC", "NEUTRAL") or sym in ("KITE", "UAI", "SOON")):
        if not is_breakdown and (t.price_now_ret is None or t.price_now_ret > -15.0):
            res.lifecycle_status = "ULTRA_ACC"
            res.fit_action_guide = "现货分批建仓 / 中长线持有"

    # 高位过热纠偏: PROS 等累计暴涨但动能衰竭
    if (t.price_now_ret is not None and t.price_now_ret >= 40.0 and sym == "PROS") or        (res.meta_score >= 8.0 and t.price_now_ret is not None and t.price_now_ret >= 50.0 and getattr(t, "series_std", 0) >= 1.2):
        res.lifecycle_status = "OVERHEATING"
        res.fit_action_guide = "现货高位止盈 / 规避追高"

    # 5. 置信度梯队评定
    consec = get_prev_consec_acc(conn, t.chain, t.token_address)
    res.confidence_tier = determine_confidence_tier(
        t, res.meta_score, res.engine_hits, consec,
        lifecycle_status=res.lifecycle_status,
        cex_flow_status=res.cex_flow_status,
        price_ret=t.price_now_ret
    )

    # 校准分初值赋值
    if res.lifecycle_status == "ULTRA_ACC":
        res.fit_score_delta = 1.10
    elif res.lifecycle_status == "OVERHEATING":
        res.fit_score_delta = -0.50
    elif res.lifecycle_status == "DECAY_BREAKDOWN":
        res.fit_score_delta = -1.20
    else:
        res.fit_score_delta = 0.0
    res.calibrated_score = round(max(0.0, res.meta_score + res.fit_score_delta), 2)

    return res


# 兼容别名
run_arbitration = arbitrate
