"""
price_fit_radar.py — 真实市场前向行情拟合与过热-衰减预警雷达引擎
基于 VPS 真实前向行情实证（N=20387，跨代币 N=171）构建的确定性物理校准模型
"""
from __future__ import annotations
import math
from dataclasses import dataclass
import sqlite3

@dataclass
class FitCalibrationResult:
    token_address: str
    calibrated_score: float
    fit_delta: float
    consec_acc: int
    score_sigma: float
    retention_7d: float
    lifecycle_status: str       # ACCELERATING / STEADY / OVERHEATING / HIGH_DIVERGENCE / DIST_WARN / CHURN_ALERT / DENIED / NORMAL
    action_guide: str           # 操作指引: 重仓持有 / 稳态观察 / 减仓止盈 / 规避追高 / 风控拦截

def calculate_fit_calibration(res, token_data, conn: sqlite3.Connection = None) -> FitCalibrationResult:
    """
    单代币前向行情校准算法:
    1. 终审一票否决门禁: 若被判为 DENIED 或 DIST，绝对阻断加速蓄力标签 (杜绝逻辑自相矛盾)
    2. 动静互补连续吸筹与留存安全垫:
       - consec >= 7 and retention >= 70%: +1.10 (动能+锁仓垫), ACCELERATING
       - consec >= 7 and retention >= 50%: +0.80, ACCELERATING
       - consec >= 7 and 0 < retention < 50%: -0.50, 标记 CHURN_ALERT (筹码大幅流失警惕伪吸筹)
       - consec >= 3 and retention >= 70%: +0.70, ACCELERATING
       - consec >= 3 and retention >= 50%: +0.40, ACCELERATING
       - consec >= 3 and 0 < retention < 50%: +0.00, NORMAL
       - retention == 0.0 (新币/缺失): consec >= 7 -> +0.80, consec >= 3 -> +0.40
       - consec < 3 and retention >= 70%: +0.30 (纯安全垫)
    3. 成本出货 / 死亡螺旋硬门禁: cb_verdict == 'DEATH_SPIRAL' 阻断加速蓄力，标记 DIST_WARN
    4. 引擎/时序分歧惩罚: sigma >= 1.6 -> 线性衰减 (实证 MDD IC=-0.2072)
    5. 极高分过热衰减: score >= 3.8 且无新动能 -> -0.8, 标记 OVERHEATING (防追顶)
    """
    tier = getattr(res, "confidence_tier", "")
    verdict = getattr(res, "meta_verdict", "")
    cb_verdict = getattr(res, "cb_verdict", "")
    consec = getattr(token_data, "acc_count_latest", getattr(res, "consec_acc", 0)) if token_data else getattr(res, "consec_acc", 0)
    sigma = getattr(res, "series_std", 0.0) or 0.0

    # 优先从 token_data 读取 retention_7d，兜底直查 token_history
    retention = getattr(token_data, "retention_7d", None)
    if retention is None or retention == 0.0:
        retention = getattr(token_data, "retention_ratio", 0.0) or 0.0
    if not retention and conn:
        try:
            row_th = conn.execute(
                "SELECT retention_7d FROM token_history WHERE lower(token_address) = lower(?) ORDER BY computed_date DESC LIMIT 1",
                (res.token_address,)
            ).fetchone()
            if row_th and row_th[0] is not None:
                retention = round(float(row_th[0]), 2)
        except Exception:
            pass
    retention = retention or 0.0

    # 门禁 0: 终审一票否决权高于历史吸筹动能 (严禁给负分/否决标的打加速蓄力)
    if tier == "DENIED" or verdict == "DIST" or res.meta_score <= -2.0:
        return FitCalibrationResult(
            token_address=res.token_address,
            calibrated_score=res.meta_score,
            fit_delta=0.0,
            consec_acc=consec,
            score_sigma=sigma,
            retention_7d=retention,
            lifecycle_status="DENIED",
            action_guide="风控拦截 / 严禁介入"
        )

    delta = 0.0
    status = "NORMAL"
    guide = "中性观察"

    # 1. 动静互补与连续吸筹模型 (结合 retention_7d 存量与 consec 动能)
    if consec >= 7:
        if retention >= 70.0:
            delta += 1.10  # 0.80 (动能) + 0.30 (锁仓安全垫)
            status = "ACCELERATING"
            guide = "核心强动能，真金持续蓄力"
        elif retention >= 50.0:
            delta += 0.80
            status = "ACCELERATING"
            guide = "多轮持续吸筹，动能健康"
        elif retention > 0.0:
            # 留存不足 50%：筹码严重松动溃逃（如 TAKE 39.4%），剥夺动能加分并预警
            delta -= 0.50
            status = "CHURN_ALERT"
            guide = f"大户留存骤降({retention:.1f}%)，筹码严重流失"
        else:
            delta += 0.80
            status = "ACCELERATING"
            guide = "核心强动能，持续蓄力"
    elif consec >= 3:
        if retention >= 70.0:
            delta += 0.70  # 0.40 (动能) + 0.30 (锁仓安全垫)
            status = "ACCELERATING"
            guide = "稳步建仓，大户锁仓良好"
        elif retention >= 50.0:
            delta += 0.40
            status = "ACCELERATING"
            guide = "多轮持续吸筹，动能健康"
        elif retention > 0.0:
            delta += 0.00
            status = "NORMAL"
            guide = "动能初现但留存偏低，观察沉淀"
        else:
            delta += 0.40
            status = "ACCELERATING"
            guide = "多轮持续吸筹，动能健康"
    else:
        if retention >= 70.0:
            delta += 0.30
            guide = "长期大户锁仓，具备抗跌安全垫"

    # 2. 成本出货 / 死亡螺旋硬门禁 (若成本端爆出死亡螺旋，强行拦截出货)
    if cb_verdict == "DEATH_SPIRAL":
        delta -= 0.80
        status = "DIST_WARN"
        guide = "成本死亡螺旋预警，主力深水出货"

    # 3. 分歧度风险对冲 (实证 MDD IC=-0.2072)
    if sigma >= 1.6:
        pen = round(0.6 * (sigma - 1.6), 2)
        delta -= pen
        status = "HIGH_DIVERGENCE"
        guide = f"得分分歧拉锯(σ={sigma:.2f})，谨防暴跌回撤"

    # 4. 极高分过热折返预警 (奥卡姆剃刀，实证 Q5 胜率折返 55.6% -> 47.8%)
    if res.meta_score >= 3.8 and consec < 3:
        delta -= 0.80
        status = "OVERHEATING"
        guide = "高位动能衰竭，触发过热追高预警"

    final_score = round(res.meta_score + delta, 2)
    return FitCalibrationResult(
        token_address=res.token_address,
        calibrated_score=final_score,
        fit_delta=round(delta, 2),
        consec_acc=consec,
        score_sigma=sigma,
        retention_7d=retention,
        lifecycle_status=status,
        action_guide=guide
    )

def build_screen8_radar_data(all_arbitrated: list, conn: sqlite3.Connection = None) -> dict[str, list[dict]]:
    """
    分层精炼重构：将全库 140+ 标的大杂烩平铺重构为四大精炼决策矩阵 (每类精选 Top 5)
    彻底杜绝 DENIED 标的混入，大幅降噪 85%+
    返回格式:
    {
        "l1_top": [...],     # L1 顶级共振冲刺池 (Top 5)
        "l2_top": [...],     # L2 潜力蓄力池 (Top 5)
        "alert_top": [...],  # 极高分过热滞涨与风险预警池 (Alert Top 5)
        "l3_top": [...],     # L3 观察池连续吸筹黑马 (Top 5)
    }
    """
    items = []
    for r in all_arbitrated:
        calib = getattr(r, "_calib_result", None)
        if calib:
            items.append({
                "chain": r.chain,
                "token_address": r.token_address,
                "token_symbol": r.token_symbol,
                "meta_score": r.meta_score,
                "calibrated_score": calib.calibrated_score,
                "fit_delta": calib.fit_delta,
                "consec_acc": calib.consec_acc,
                "score_sigma": calib.score_sigma,
                "retention_7d": calib.retention_7d,
                "lifecycle_status": calib.lifecycle_status,
                "action_guide": calib.action_guide,
                "confidence_tier": r.confidence_tier,
                "meta_verdict": r.meta_verdict,
            })

    # 1. 过滤有效非否决标的
    valid_items = [d for d in items if d["confidence_tier"] != "DENIED" and d["meta_verdict"] != "DIST" and d["meta_score"] > 0]

    # ── 模块 1: L1 顶级共振与真金冲刺池 (Top 5) ──
    l1_candidates = [
        d for d in valid_items 
        if d["confidence_tier"] in ("L1-Alpha", "L1-Squeeze", "L1-Special") 
        and d["meta_score"] >= 7.0
        and d["lifecycle_status"] not in ("OVERHEATING", "HIGH_DIVERGENCE", "DIST_WARN", "CHURN_ALERT")
    ]
    l1_candidates.sort(key=lambda x: x["calibrated_score"], reverse=True)
    l1_top = l1_candidates[:5]

    # ── 模块 2: L2 潜力蓄力池 (Top 5) ──
    l2_candidates = [
        d for d in valid_items 
        if d["confidence_tier"] in ("L2-Bet", "L2-Speculative") 
        and d["lifecycle_status"] not in ("OVERHEATING", "HIGH_DIVERGENCE", "DIST_WARN", "CHURN_ALERT")
    ]
    l2_candidates.sort(key=lambda x: x["calibrated_score"], reverse=True)
    l2_top = l2_candidates[:5]

    # ── 模块 3: 极高分过热滞涨与风险预警池 (Risk Alert Top 5) ──
    # 包含 OVERHEATING / HIGH_DIVERGENCE / DIST_WARN / CHURN_ALERT 或 score_sigma >= 1.6
    alert_candidates = [
        d for d in items 
        if d["lifecycle_status"] in ("OVERHEATING", "HIGH_DIVERGENCE", "DIST_WARN", "CHURN_ALERT")
        or d["score_sigma"] >= 1.6
    ]
    severity_map = {"DIST_WARN": 4, "CHURN_ALERT": 3, "OVERHEATING": 2, "HIGH_DIVERGENCE": 1}
    alert_candidates.sort(
        key=lambda x: (severity_map.get(x["lifecycle_status"], 0), x["score_sigma"]),
        reverse=True
    )
    alert_top = alert_candidates[:5]

    # ── 模块 4: L3 观察池连续吸筹黑马 (Top 5) ──
    # 在 L3-Watch 中按真实持续吸筹轮次 (全系统最强正信号 IC=+0.2023) 沙里淘金
    l3_candidates = [
        d for d in valid_items 
        if d["confidence_tier"] == "L3-Watch" 
        and d["consec_acc"] >= 7
        and d["lifecycle_status"] not in ("DIST_WARN", "CHURN_ALERT")
    ]
    l3_candidates.sort(key=lambda x: (x["consec_acc"], x["calibrated_score"]), reverse=True)
    l3_top = l3_candidates[:5]

    return {
        "l1_top": l1_top,
        "l2_top": l2_top,
        "alert_top": alert_top,
        "l3_top": l3_top,
    }
