"""
opus-scan — 联网情报 (v2: 含 Gecko Pool 增强)
可降级：联网不可用时仅返回 DB 已有数据。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import time
import config


@dataclass
class MarketContext:
    """联网市场上下文"""
    has_data: bool = False

    # Gecko DB 缓存
    price_usd: Optional[float] = None
    fdv_usd: Optional[float] = None
    volume_24h: Optional[float] = None
    lp_usd: Optional[float] = None
    price_change_24h: Optional[float] = None

    # Pool 增强 (方案 A)
    price_change_7d: Optional[float] = None
    pool_lp_usd: Optional[float] = None
    pool_volume_24h: Optional[float] = None
    pool_buys_24h: Optional[int] = None
    pool_sells_24h: Optional[int] = None
    pool_buyers_24h: Optional[int] = None
    pool_sellers_24h: Optional[int] = None

    # 计算指标
    buy_sell_person_ratio: Optional[float] = None
    volume_declining: bool = False
    lp_thin: bool = False
    vl_ratio: float = 0.0
    mcap_liq_ratio: float = 0.0

    # 联网状态
    gecko_api_ok: bool = False
    gecko_pool_ok: bool = False


def research(
    chain: str,
    address: str,
    gecko_db: Optional[dict] = None,
    proxy: Optional[str] = None,
    online: bool = False,
) -> MarketContext:
    ctx = MarketContext()
    _proxy = proxy or config.PROXY_URL

    # 1. DB Gecko 数据（零成本）
    if gecko_db:
        ctx.has_data = True
        ctx.price_usd = gecko_db.get("price_usd")
        ctx.fdv_usd = gecko_db.get("fdv_usd")
        ctx.volume_24h = gecko_db.get("volume_24h_usd") or gecko_db.get("volume_24h")
        ctx.lp_usd = gecko_db.get("reserve_usd") or gecko_db.get("lp_usd")
        ctx.price_change_24h = gecko_db.get("price_change_24h")
        ctx.vl_ratio = gecko_db.get("vl_ratio") or 0
        ctx.mcap_liq_ratio = gecko_db.get("mcap_liq_ratio") or 0

    # 2. 联网 GeckoTerminal API
    if online:
        # Token 基础信息
        live = _fetch_gecko_token(chain, address, _proxy)
        if live:
            ctx.has_data = True
            ctx.gecko_api_ok = True
            if live.get("price"):
                ctx.price_usd = live["price"]
            if live.get("change_24h") is not None:
                ctx.price_change_24h = live["change_24h"]

        # Pool 增强 (方案 A)
        time.sleep(1.5)  # 避免 429
        pool = _fetch_gecko_pools(chain, address, _proxy)
        if pool:
            ctx.gecko_pool_ok = True
            ctx.pool_lp_usd = pool.get("lp_usd")
            ctx.pool_volume_24h = pool.get("volume_24h")
            ctx.pool_buys_24h = pool.get("buys_24h")
            ctx.pool_sells_24h = pool.get("sells_24h")
            ctx.pool_buyers_24h = pool.get("buyers_24h")
            ctx.pool_sellers_24h = pool.get("sellers_24h")
            ctx.price_change_7d = pool.get("price_change_7d")

            # LP 判定
            if ctx.pool_lp_usd is not None:
                ctx.lp_usd = ctx.pool_lp_usd
                ctx.lp_thin = ctx.pool_lp_usd < config.POOL_LP_THIN_USD

            # 买卖人数比
            buyers = ctx.pool_buyers_24h or 0
            sellers = ctx.pool_sellers_24h or 1
            if sellers > 0:
                ctx.buy_sell_person_ratio = round(buyers / sellers, 2)

    return ctx


def _fetch_gecko_token(
    chain: str, address: str, proxy: Optional[str] = None
) -> Optional[dict]:
    try:
        import httpx
        net = config.CHAIN_MAP.get(chain, chain)
        url = f"{config.GECKO_API_BASE}/networks/{net}/tokens/{address}"
        kwargs = {"timeout": 10, "verify": False}
        if proxy:
            kwargs["proxy"] = proxy
        r = httpx.get(url, **kwargs)
        if r.status_code == 200:
            data = r.json().get("data", {}).get("attributes", {})
            pct = data.get("price_change_percentage") or {}
            return {
                "price": _to_float(data.get("price_usd")),
                "change_24h": _to_float(pct.get("h24")),
            }
        if r.status_code == 429:
            time.sleep(4)
    except Exception:
        pass
    return None


def _fetch_gecko_pools(
    chain: str, address: str, proxy: Optional[str] = None
) -> Optional[dict]:
    """获取代币的主流动池数据（取 Volume 最大的池）"""
    try:
        import httpx
        net = config.CHAIN_MAP.get(chain, chain)
        url = f"{config.GECKO_API_BASE}/networks/{net}/tokens/{address}/pools?page=1"
        kwargs = {"timeout": 10, "verify": False}
        if proxy:
            kwargs["proxy"] = proxy
        r = httpx.get(url, **kwargs)
        if r.status_code == 429:
            time.sleep(4)
            r = httpx.get(url, **kwargs)
        if r.status_code != 200:
            return None

        pools = r.json().get("data", [])
        if not pools:
            return None

        # 取第一个池（默认按 Volume 排序）
        p = pools[0].get("attributes", {})
        txns = p.get("transactions") or {}
        h24 = txns.get("h24") or {}
        pct = p.get("price_change_percentage") or {}

        return {
            "lp_usd": _to_float(p.get("reserve_in_usd")),
            "volume_24h": _to_float((p.get("volume_usd") or {}).get("h24")),
            "buys_24h": h24.get("buys", 0),
            "sells_24h": h24.get("sells", 0),
            "buyers_24h": h24.get("buyers", 0),
            "sellers_24h": h24.get("sellers", 0),
            "price_change_7d": _to_float(pct.get("d7")),  # 可能不存在
        }
    except Exception:
        pass
    return None


def _to_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
