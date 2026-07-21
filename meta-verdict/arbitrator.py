"""
meta-verdict 仲裁引擎
5 引擎投票 → 加权积分 → 统一排名 + 生命周期状态机
"""
from __future__ import annotations
from dataclasses import dataclass, field
import config
import sqlite3
from collector import TokenEngineData


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

    # 价格数据（来自 cost-basis-scan）
    cb_gecko_price: float = 0.0
    cb_vwap:        float = 0.0
    cb_windfall_pct:float = 0.0
    cb_acc_pct:     float = 0.0
    cb_dist_pct:    float = 0.0
    cb_signals:     str   = ""

    # 生命周期
    stage: str = ""   # ACCUMULATING / CONTROLLED / DISTRIBUTING / WATCHLIST / NEUTRAL


def get_prev_consec_acc(conn: sqlite3.Connection, chain: str, token_address: str) -> int:
    """查询该代币在上一轮运行前的连续 ACC 轮次"""
    if conn is None:
        return 0
    try:
        cursor = conn.execute("""
            SELECT meta_verdict FROM meta_snapshots
            WHERE chain = ? AND token_address = ?
            ORDER BY scan_time DESC LIMIT 30
        """, (chain, token_address.lower()))
        rows = cursor.fetchall()
        consec = 0
        for r in rows:
            if r[0] == "ACC":
                consec += 1
            else:
                break
        return consec
    except Exception:
        return 0



def _check_oscillation_suppression(conn: sqlite3.Connection, chain: str, token_address: str) -> bool:
    """检查 72h 内是否有 >= 3 次判定翻转，若存在且近期属于 ACC 阶段则抑制一票否决"""
    if conn is None:
        return False
    try:
        cursor = conn.execute("""
            SELECT meta_verdict, stage FROM meta_snapshots
            WHERE chain = ? AND token_address = ? AND scan_time >= datetime('now', '-3 days')
            ORDER BY scan_time DESC
        """, (chain, token_address.lower()))
        rows = cursor.fetchall()
        if len(rows) < 4:
            return False
        
        flips = 0
        for i in range(1, len(rows)):
            if rows[i][0] != rows[i-1][0]:
                flips += 1
        
        has_acc_stage = any("ACC" in (r[1] or "") for r in rows[:6])
        return flips >= 3 and has_acc_stage
    except Exception:
        return False


def arbitrate(data: TokenEngineData, hop2_pct: float = 0.0, conn: sqlite3.Connection = None, scan_time: str = None) -> MetaResult:
    """5 引擎加权积分仲裁"""
    r = MetaResult(
        chain=data.chain,
        token_address=data.token_address,
        token_symbol=data.token_symbol,
        engine_hits=data.engine_hits,
        master_signal=data.master_signal,
        opus_verdict=data.opus_verdict,
        unified_signal=data.unified_signal,
        whale_level=data.whale_level,
        cb_verdict=data.cb_verdict,
        cb_gecko_price=data.cb_gecko_price,
        cb_vwap=data.cb_vwap,
        cb_windfall_pct=data.cb_windfall_pct,
        cb_acc_pct=data.cb_acc_pct,
        cb_dist_pct=data.cb_dist_pct,
        cb_signals=data.cb_signals,
    )

    # ── Volume & LP Guard 绝对流动性门控 (一刀切退化死币) ──
    volume_24h = 100000.0
    reserve_usd = 100000.0
    try:
        with sqlite3.connect(config.SRC_DB_PATH) as src_conn:
            src_conn.row_factory = sqlite3.Row
            row = src_conn.execute(
                "SELECT volume_24h, reserve_usd FROM gecko_market_data "
                "WHERE token_address=? ORDER BY scan_time DESC LIMIT 1",
                (data.token_address,)
            ).fetchone()
            if row:
                volume_24h = row["volume_24h"] if row["volume_24h"] is not None else 0.0
                reserve_usd = row["reserve_usd"] if row["reserve_usd"] is not None else 0.0
    except Exception:
        pass

    is_liquidity_dead = (volume_24h < 1000.0 or reserve_usd < 10000.0)
    if is_liquidity_dead:
        r.meta_score = 0.0
        r.meta_score_smooth = 0.0
        r.meta_verdict = "NEUTRAL"
        r.stage = "NEUTRAL"
        return r

    # ── consec_acc 持续期强校验 (降级单轮/偶发 DIAMOND 噪音) ──
    prev_consec = get_prev_consec_acc(conn, data.chain, data.token_address)

    # ── master-scan 积分 ──
    master_map = {
        "DIAMOND": config.MASTER_DIAMOND,
        "RED":     config.MASTER_RED,
        "YELLOW":  config.MASTER_YELLOW,
    }
    
    # ── 假出货豁免 (套牢盘 RED 信号降级为 YELLOW) ──
    pnl = None
    if data.cb_vwap and data.cb_vwap > 0:
        pnl = (data.cb_gecko_price - data.cb_vwap) / data.cb_vwap * 100
        
    is_underwater_resilient = (pnl is not None and pnl < -30.0)
    
    if r.master_signal == "RED" and is_underwater_resilient:
        r.master_signal = "YELLOW"

    if r.master_signal == "DIAMOND" and prev_consec < 5:
        # 单轮或偶发 DIAMOND 降级为 YELLOW 积分
        r.master_score = config.MASTER_YELLOW
    else:
        r.master_score = master_map.get(r.master_signal, 0)

    # ── opus-scan 积分（正向吸筹 / 负向出货）──
    r.opus_score = round(data.opus_acc_conf * config.OPUS_ACC_SCALE
                         - data.opus_dist_conf * config.OPUS_DIST_SCALE, 2)

    # ── unified-scan 积分（吸筹方向 + 出货方向）──
    if data.unified_signal in config.UNIFIED_DIST_SCORE:
        r.unified_score = config.UNIFIED_DIST_SCORE[data.unified_signal]
    else:
        r.unified_score = config.UNIFIED_SCORE.get(data.unified_signal, 0)

    # ── whale-scan 积分 ──
    whale_map = {
        "HIGH":   config.WHALE_HIGH,
        "MEDIUM": config.WHALE_MEDIUM,
        "LOW":    config.WHALE_LOW,
    }
    r.whale_score = whale_map.get(data.whale_level, 0)

    # ── cost-basis-scan 积分 ──
    r.cb_score = config.CB_SCORE.get(data.cb_verdict, 0)

    # ── hop2 积分贡献与成本价格联动 (大户大捷期清零吸筹) ──
    if pnl is not None and pnl >= 50.0:
        # 已暴涨 50% 以上，进入分发区，清空 hop2 贡献分
        r.hop2_score = 0.0
    else:
        if hop2_pct >= 0.30:
            r.hop2_score = 1.50
        elif hop2_pct >= 0.15:
            r.hop2_score = 0.80
        else:
            r.hop2_score = 0.0

    # ── master/unified DIAMOND 信号去重（仅 DIAMOND 同源去重）──
    if r.master_signal == "DIAMOND" and r.unified_signal == "DIAMOND":
        r.unified_score = round(r.unified_score * 0.5, 2)

    # ── 出货方向 master 抑制 ──
    # 当 opus/unified 明确出货时，master 正分不应抵消出货积分
    is_dist_signal = (
        data.opus_verdict == "SLOW_DISTRIBUTION"
        or data.unified_signal in ("SLOW_DIST", "WHALE_DUMP")
        or data.cb_verdict in ("DEATH_SPIRAL", "LIQUIDITY_CRISIS")
    )
    if is_dist_signal and r.master_score > 0:
        r.master_score = 0

    # ── 综合积分 ──
    r.meta_score = round(
        r.master_score + r.opus_score + r.unified_score + r.whale_score + r.cb_score + r.hop2_score, 2
    )

    # ── EWMA_3 不对称平滑分 (防暴雷滞后) ──
    history_scores = []
    if conn is not None and scan_time:
        try:
            cursor = conn.execute("""
                SELECT meta_score FROM meta_snapshots
                WHERE chain = ? AND token_address = ? AND scan_time < ?
                ORDER BY scan_time DESC LIMIT 2
            """, (data.chain, data.token_address.lower(), scan_time))
            history_scores = [row[0] for row in cursor.fetchall()]
        except Exception:
            pass

    current_score = r.meta_score
    r.meta_score_smooth = current_score

    if len(history_scores) >= 1:
        prev_score = history_scores[0]
        # 不对称平滑熔断：跌幅超 1.5 分（约跌 20% 以上）则直接熔断平滑以防预警滞后
        is_sudden_drop = (current_score < prev_score) and ((prev_score - current_score) >= 1.5)
        
        if not is_sudden_drop:
            if len(history_scores) >= 2:
                r.meta_score_smooth = round(0.6 * current_score + 0.3 * history_scores[0] + 0.1 * history_scores[1], 2)
            else:
                r.meta_score_smooth = round((0.6 * current_score + 0.3 * history_scores[0]) / 0.9, 2)
        else:
            r.meta_score_smooth = current_score

    # ── 假说2: 加速吸筹防御性共振判定与加分 ──
    is_accelerating = (
        (data.diff_acc_count >= 5 or data.new_acc_count >= 5)
        and (data.diff_acc_score >= 3.0 or data.diff_stay_score >= 2.0 or data.diff_total_score >= 200)
        and data.vl_ratio >= 5.0
    )
    if is_accelerating:
        r.meta_score = round(r.meta_score + 1.5, 2)
        r.meta_score_smooth = round(r.meta_score_smooth + 1.5, 2)

    # ── 洗盘换手惩罚 (防御情况1) ──
    total_acc = (data.diff_acc_count + data.new_acc_count)  # 估算当前吸筹数
    turnover_rate = data.new_acc_count / total_acc if total_acc > 0 else 0.0
    
    is_wash_trading = (
        data.new_acc_count >= 2
        and data.diff_acc_count <= 0
        and turnover_rate >= 0.20
        and total_acc >= 3
    )

    if is_wash_trading:
        r.meta_score = round(r.meta_score - 1.5, 2)
        r.meta_score_smooth = round(r.meta_score_smooth - 1.5, 2)

    # ── 裁决 ──
    if r.meta_score >= config.META_ACC_THRESHOLD:
        r.meta_verdict = "ACC"
    elif r.meta_score <= config.META_DIST_THRESHOLD:
        r.meta_verdict = "DIST"
    else:
        r.meta_verdict = "NEUTRAL"

    # ── 生命周期阶段 ──
    r.stage = _infer_stage(r, data, is_accelerating, is_wash_trading)

    return r


def _infer_stage(r: MetaResult, data: TokenEngineData, is_accelerating: bool = False, is_wash_trading: bool = False) -> str:
    """推断代币生命周期阶段"""
    if is_wash_trading:
        return "DISTRIBUTING"

    if is_accelerating:
        return "ACC_ACCELERATING"

    # 极度控盘：master DIAMOND + whale HIGH
    if r.master_signal == "DIAMOND" and r.whale_level == "HIGH":
        return "CONTROLLED"

    # 出货末期
    if r.cb_verdict in ("DEATH_SPIRAL", "LIQUIDITY_CRISIS"):
        return "DISTRIBUTING"

    # 主动派发
    if r.meta_verdict == "DIST":
        return "DISTRIBUTING"

    # 吸筹阶段
    if r.meta_verdict == "ACC":
        if r.master_signal == "DIAMOND":
            return "CONTROLLED"
        return "ACCUMULATING"

    # 有 master 信号但综合未达阈值 → 观察
    if r.master_signal in ("RED", "YELLOW"):
        return "WATCHLIST"

    return "NEUTRAL"


def run_arbitration(all_data: list[TokenEngineData]) -> tuple[list[MetaResult], list[MetaResult]]:
    """批量仲裁，返回 (acc排行, dist预警)"""
    results = [arbitrate(d) for d in all_data]

    acc_list  = sorted(
        [r for r in results if r.meta_verdict == "ACC"],
        key=lambda r: r.meta_score, reverse=True
    )
    dist_list = sorted(
        [r for r in results if r.meta_verdict == "DIST"],
        key=lambda r: r.meta_score
    )
    return acc_list, dist_list
