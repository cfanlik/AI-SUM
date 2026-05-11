#!/usr/bin/env python3
"""外部 API 市场数据采集器 — 周月报专用
所有请求走 socks5://127.0.0.1:42000 代理, 不可用时 fallback 直连
"""
import httpx, time, logging, json
from datetime import datetime, timedelta

logger = logging.getLogger("market_context")
PROXY = "socks5://127.0.0.1:42000"
UA = "Mozilla/5.0 (X11; Linux x86_64) AI-SUM/1.0"


def _client(timeout=30):
    """带代理的 httpx 客户端, 代理不可用 fallback 直连"""
    headers = {"User-Agent": UA}
    try:
        httpx.get("https://api.coingecko.com/api/v3/ping",
                   proxy=PROXY, timeout=5, verify=False, headers=headers)
        return httpx.Client(proxy=PROXY, timeout=timeout, verify=False, headers=headers)
    except Exception:
        logger.warning("42000 代理不可用, fallback 直连")
        return httpx.Client(timeout=timeout, verify=False, headers=headers)


# ── 1. BTC/ETH K线 (Binance) ──
def fetch_btc_klines(interval="1w", limit=4, symbols=None):
    """获取 BTC/ETH/BNB 周K线"""
    if symbols is None:
        symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
    results = {}
    with _client() as c:
        for sym in symbols:
            try:
                r = c.get(f"https://api.binance.com/api/v3/klines",
                          params={"symbol": sym, "interval": interval, "limit": limit})
                data = r.json()
                results[sym] = [{
                    "ts": datetime.fromtimestamp(k[0]/1000).strftime("%Y-%m-%d"),
                    "open": float(k[1]), "high": float(k[2]),
                    "low": float(k[3]), "close": float(k[4]),
                    "volume": float(k[5]),
                } for k in data]
                time.sleep(0.2)
            except Exception as e:
                logger.error(f"Binance kline {sym}: {e}")
                results[sym] = []
    return results


# ── 2. CoinGecko 全球数据 ──
def fetch_global_market():
    """全球市值, BTC 占比, 24h 成交量"""
    with _client() as c:
        try:
            r = c.get("https://api.coingecko.com/api/v3/global")
            d = r.json()["data"]
            return {
                "btc_dominance": d["market_cap_percentage"]["btc"],
                "eth_dominance": d["market_cap_percentage"].get("eth", 0),
                "total_market_cap": d["total_market_cap"]["usd"],
                "total_volume_24h": d["total_volume"]["usd"],
                "active_coins": d["active_cryptocurrencies"],
                "market_cap_change_24h": d.get("market_cap_change_percentage_24h_usd", 0),
            }
        except Exception as e:
            logger.error(f"CoinGecko global: {e}")
            return {}


# ── 3. BTC 历史价格 (CoinGecko) ──
def fetch_btc_history(days=7):
    """BTC 日线价格序列"""
    with _client() as c:
        try:
            time.sleep(2)
            r = c.get("https://api.coingecko.com/api/v3/coins/bitcoin/market_chart",
                      params={"vs_currency": "usd", "days": days, "interval": "daily"})
            d = r.json()
            prices = [{
                "date": datetime.fromtimestamp(p[0]/1000).strftime("%Y-%m-%d"),
                "price": p[1]
            } for p in d.get("prices", [])]
            return prices
        except Exception as e:
            logger.error(f"CoinGecko BTC history: {e}")
            return []


# ── 4. Fear & Greed Index ──
def fetch_fear_greed(limit=30):
    """恐贪指数序列"""
    with _client() as c:
        try:
            r = c.get(f"https://api.alternative.me/fng/", params={"limit": limit})
            d = r.json()
            return [{
                "date": datetime.fromtimestamp(int(item["timestamp"])).strftime("%Y-%m-%d"),
                "value": int(item["value"]),
                "label": item["value_classification"],
            } for item in d.get("data", [])]
        except Exception as e:
            logger.error(f"Fear&Greed: {e}")
            return []


# ── 5. GeckoTerminal OHLCV (带 fallback) ──
def fetch_ohlcv_daily(chain, pool_address, days=7):
    """池子日线 OHLCV, 失败返回空"""
    network = {"bsc": "bsc", "eth": "eth", "sol": "solana"}.get(chain, chain)
    with _client() as c:
        try:
            r = c.get(
                f"https://api.geckoterminal.com/api/v2/networks/{network}"
                f"/pools/{pool_address}/ohlcv/day",
                params={"aggregate": 1, "limit": days})
            ohlcv = r.json().get("data", {}).get("attributes", {}).get("ohlcv_list", [])
            return [{
                "date": datetime.fromtimestamp(o[0]).strftime("%Y-%m-%d"),
                "open": o[1], "high": o[2], "low": o[3], "close": o[4], "volume": o[5],
            } for o in ohlcv]
        except Exception as e:
            logger.debug(f"GeckoTerminal OHLCV {pool_address}: {e}")
            return []


def collect_all(days=7):
    """一次性采集所有外部数据"""
    logger.info(f"采集外部市场数据 (days={days})...")
    t0 = time.time()
    ctx = {
        "btc_klines": fetch_btc_klines("1w", 4),
        "global_market": fetch_global_market(),
        "btc_history": fetch_btc_history(days),
        "fear_greed": fetch_fear_greed(min(days + 7, 30)),
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    logger.info(f"外部数据采集完成 ({time.time()-t0:.1f}s)")
    return ctx


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if "--test" in sys.argv:
        ctx = collect_all(7)
        for k, v in ctx.items():
            if isinstance(v, list):
                print(f"{k}: {len(v)} 条")
            elif isinstance(v, dict):
                print(f"{k}: {json.dumps(v, indent=2, default=str)[:200]}")
            else:
                print(f"{k}: {v}")
    else:
        print("用法: python3 market_context.py --test")
