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
S2: M/L 泡沫比 + S3: Gecko Pool 买卖人数比
来源: opus-scan web_researcher + bigcoin S4
"""
from __future__ import annotations
from typing import Optional
import time
import config


def check_market(gecko_db: Optional[dict], chain: str, addr: str) -> dict:
    """S2(离线) + S3(联网) 市场结构分析。"""
    r = {"s2_triggered": False, "s3_acc_triggered": False, "s3_dist_triggered": False,
         "mcap_liq_ratio": 0.0, "buy_sell_ratio": None,
         "pool_volume_24h": None, "pool_lp_usd": None,
         "vl_ratio": 0.0, "s4_vl_triggered": False}

    # S2: M/L 泡沫比（从DB）
    if gecko_db:
        # 优先使用 gecko_market_data 中预计算的 mcap_liq_ratio
        pre_ratio = gecko_db.get("mcap_liq_ratio") or 0
        if pre_ratio > 0:
            r["mcap_liq_ratio"] = round(pre_ratio, 1)
        else:
            # fallback: 手动计算，优先 FDV，其次 market_cap
            reserve = gecko_db.get("reserve_usd") or 0
            valuation = gecko_db.get("fdv_usd") or gecko_db.get("market_cap_usd") or 0
            if reserve > 0 and valuation > 0:
                r["mcap_liq_ratio"] = round(valuation / reserve, 1)
        r["s2_triggered"] = r["mcap_liq_ratio"] > config.S2_MCAP_LIQ_THRESHOLD

    # S4: V/L 换手效率
    if gecko_db:
        vl = gecko_db.get("vl_ratio") or 0
        r["vl_ratio"] = round(vl, 2)
        r["s4_vl_triggered"] = vl > config.S4_VL_HIGH_THRESHOLD

    # S3: Gecko Pool 联网增强
    if config.S3_ENABLED:
        pool = _fetch_gecko_pool(chain, addr)
        if pool:
            r["pool_volume_24h"] = pool.get("volume_24h")
            r["pool_lp_usd"] = pool.get("lp_usd")
            buyers = pool.get("buyers_24h") or 0
            sellers = pool.get("sellers_24h") or 1
            if sellers > 0:
                ratio = round(buyers / sellers, 2)
                r["buy_sell_ratio"] = ratio
                r["s3_acc_triggered"] = ratio >= config.S3_ACC_PERSON_THRESHOLD
                r["s3_dist_triggered"] = ratio < config.S3_BUY_SELL_PERSON_THRESHOLD

    return r


def _fetch_gecko_pool(chain: str, addr: str) -> Optional[dict]:
    try:
        import httpx
        net = config.CHAIN_MAP.get(chain, chain)
        url = f"{config.GECKO_API_BASE}/networks/{net}/tokens/{addr}/pools?page=1"
        kwargs = {"timeout": 10, "verify": False}
        if config.PROXY_URL:
            kwargs["proxy"] = config.PROXY_URL
        r = httpx.get(url, **kwargs)
        if r.status_code == 429:
            time.sleep(4)
            r = httpx.get(url, **kwargs)
        if r.status_code != 200:
            return None
        pools = r.json().get("data", [])
        if not pools:
            return None
        p = pools[0].get("attributes", {})
        txns = p.get("transactions") or {}
        h24 = txns.get("h24") or {}
        return {
            "lp_usd": _f(p.get("reserve_in_usd")),
            "volume_24h": _f((p.get("volume_usd") or {}).get("h24")),
            "buyers_24h": h24.get("buyers", 0),
            "sellers_24h": h24.get("sellers", 0),
        }
    except Exception:
        return None


def _f(val) -> Optional[float]:
    if val is None: return None
    try: return float(val)
    except: return None
