"""
meta-verdict 引擎矛盾检测器
检测跨引擎信号不一致的代币
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Conflict:
    symbol: str
    chain: str
    score: float
    verdict: str
    rule: str       # C1/C2/C3/C4
    detail: str


def detect_conflicts(results, all_data=None) -> list[Conflict]:
    """检测引擎间信号矛盾"""
    conflicts = []

    for r in results:
        # C1: master=DIAMOND 但 whale 无数据
        if r.master_signal == "DIAMOND" and not r.whale_level:
            conflicts.append(Conflict(
                symbol=r.token_symbol, chain=r.chain,
                score=r.meta_score, verdict=r.meta_verdict,
                rule="C1", detail="master=DIAMOND 但 whale 无信号 → 庄控未验证"
            ))

        # C2: unified 含出货信号但 meta=ACC
        if r.unified_signal in ("SLOW_DIST", "WHALE_DUMP") and r.meta_verdict == "ACC":
            conflicts.append(Conflict(
                symbol=r.token_symbol, chain=r.chain,
                score=r.meta_score, verdict=r.meta_verdict,
                rule="C2", detail=f"unified={r.unified_signal} 但 meta=ACC → 引擎对立"
            ))

        # C3: CB=SQUEEZE_ACC 但 opus 无吸筹信号
        if r.cb_verdict and "SQUEEZE" in r.cb_verdict and not r.opus_verdict:
            conflicts.append(Conflict(
                symbol=r.token_symbol, chain=r.chain,
                score=r.meta_score, verdict=r.meta_verdict,
                rule="C3", detail=f"CB={r.cb_verdict} 但 opus 无数据 → 单引擎支撑"
            ))

        # C4: opus=出货 但 meta=ACC
        if r.opus_verdict == "SLOW_DISTRIBUTION" and r.meta_verdict == "ACC":
            conflicts.append(Conflict(
                symbol=r.token_symbol, chain=r.chain,
                score=r.meta_score, verdict=r.meta_verdict,
                rule="C4", detail=f"opus=出货 但 meta=ACC ({r.meta_score:.1f}) → 引擎对立"
            ))

        # C5: 单引擎高分
        if r.engine_hits == 1 and r.meta_score >= 3:
            conflicts.append(Conflict(
                symbol=r.token_symbol, chain=r.chain,
                score=r.meta_score, verdict=r.meta_verdict,
                rule="C5", detail=f"仅 1 引擎命中但 score={r.meta_score:.1f} → 信号单薄"
            ))

    # 去重（同代币同规则）
    seen = set()
    unique = []
    for c in conflicts:
        k = f"{c.symbol}:{c.rule}"
        if k not in seen:
            seen.add(k)
            unique.append(c)

    return sorted(unique, key=lambda c: c.score, reverse=True)
