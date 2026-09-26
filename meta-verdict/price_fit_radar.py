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
    lifecycle_status: str       # ACCELERATING / STEADY / OVERHEATING / HIGH_DIVERGENCE / NORMAL
    action_guide: str           # 操作指引: 重仓持有 / 稳态观察 / 减仓止盈 / 规避追高

def calculate_fit_calibration(res, token_data, conn: sqlite3.Connection = None) -> FitCalibrationResult:
    """
    单代币前向行情校准算法:
    1. 连续吸筹增益: consec >= 7 -> +0.8, consec >= 3 -> +0.4 (实证 IC=+0.2023)
    2. 引擎分歧惩罚: sigma >= 1.6 -> 线性衰减 (实证 MDD IC=-0.2072)
    3. 极高分过热衰减: score >= 3.8 且无新动能 -> -0.8, 标记 OVERHEATING (防追顶)
    4. 留存安全垫: retention_7d >= 70% -> +0.3 (实证 MDD=+0.2970 防御因子)
    """
    delta = 0.0
    consec = getattr(token_data, "acc_count_latest", 0)
    sigma = getattr(res, "series_std", 0.0) or 0.0
    retention = getattr(token_data, "retention_ratio", 0.0) or 0.0

    status = "NORMAL"
    guide = "中性观察"

    # 1. 连续吸筹正向动能 (实证 IC=+0.2023)
    if consec >= 7:
        delta += 0.80
        status = "ACCELERATING"
        guide = "核心强动能，真金持续蓄力"
    elif consec >= 3:
        delta += 0.40
        status = "ACCELERATING"
        guide = "多轮持续吸筹，动能健康"

    # 2. 7天锁仓留存安全垫 (抗跌压舱石，实证 MDD IC=+0.2970)
    if retention >= 70.0:
        delta += 0.30

    # 3. 分歧度风险对冲 (实证 MDD IC=-0.2072)
    if sigma >= 1.6:
        pen = round(0.6 * (sigma - 1.6), 2)
        delta -= pen
        status = "HIGH_DIVERGENCE"
        guide = f"引擎分歧极大(σ={sigma:.2f})，谨防暴跌回撤"

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

def build_screen8_radar_data(all_arbitrated: list, conn: sqlite3.Connection = None) -> list[dict]:
    """汇总所有代币校准结果，构建第八屏驾驶舱展示字典列表"""
    results = []
    for r in all_arbitrated:
        calib = getattr(r, "_calib_result", None)
        if calib:
            results.append({
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
            })
    # 按校准分从高到低排序
    results.sort(key=lambda x: x["calibrated_score"], reverse=True)
    return results
