import sys
sys.path.insert(0, "/opt/select-coin")
sys.path.insert(0, "/opt/AI-SUM")
import sqlite3
import urllib.request
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import get_proxy
from anomaly_watch.config import DB_PATH, AI_SUM_DB_PATH, MICRO_POOL_THRESHOLD, LARGE_FAKE_THRESHOLD, CORE_ASSETS

def clean_addr(s):
    if not s: return ""
    s = str(s).strip().lower()
    if "_" in s: s = s.split("_")[-1]
    return s

def fetch_json(url, max_retries=3):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    for attempt in range(max_retries):
        p_url = get_proxy()
        proxy_handler = urllib.request.ProxyHandler({'http': p_url, 'https': p_url}) if p_url else urllib.request.BaseHandler()
        opener = urllib.request.build_opener(proxy_handler)
        req = urllib.request.Request(url, headers=headers)
        try:
            with opener.open(req, timeout=4) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode('utf-8'))
        except Exception:
            time.sleep(0.2 * (attempt + 1))
    return None

def rpc_call(method, params):
    RPC_URL = "https://bsc-dataseed.binance.org/"
    headers = {'Content-Type': 'application/json'}
    p_url = get_proxy()
    proxy_handler = urllib.request.ProxyHandler({'http': p_url, 'https': p_url}) if p_url else urllib.request.BaseHandler()
    opener = urllib.request.build_opener(proxy_handler)
    data = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode('utf-8')
    req = urllib.request.Request(RPC_URL, data=data, headers=headers)
    try:
        with opener.open(req, timeout=4) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception:
        return None

def get_token_balance(token_address, owner_address):
    if not token_address or not owner_address or token_address == "N/A": return 0
    clean_owner = owner_address.lower().replace("0x", "").zfill(64)
    data_hex = "0x70a08231" + clean_owner
    r = rpc_call("eth_call", [{"to": token_address, "data": data_hex}, "latest"])
    if r and "result" in r and r["result"] != "0x":
        try: return int(r["result"], 16)
        except Exception: return 0
    return 0

class AnomalyAnalyzer:
    STABLECOINS = {
        "0x55d398326f99059ff775485246999027b3197955", "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
        "0xe9e7cea3dedca5984780bafc599bd69add087d56", "0x8d0d000ee44948fc98c9b98a4fa4921476f08b0d",
        "0xc5f0f7b66764f6ec8c8dff7ba683102295e16409", "0x1af3f329e8be154074d8769d1ffa4ee058b1dbc3",
        "0xdac17f958d2ee523a2206206994597c13d831ec7", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
    }
    CL_POOL_MANAGER = "0xa0ffb9c1ce1fe56963b0321b32e7a0302114058b"

    def __init__(self):
        self.db_path = DB_PATH
        self.ai_sum_db_path = AI_SUM_DB_PATH

    def load_tokens_pools_and_meta(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        query = """
        SELECT DISTINCT token_address, 'bsc' as chain FROM token_scores
        UNION
        SELECT DISTINCT token_address, 'bsc' as chain FROM token_names
        UNION
        SELECT DISTINCT token_address, 'bsc' as chain FROM bubblemap_holders
        """
        cursor.execute(query)
        tokens = cursor.fetchall()
        
        token_symbol_map = {}
        cursor.execute("SELECT DISTINCT token_address, symbol FROM token_names")
        for row in cursor.fetchall():
            token_symbol_map[row[0].lower()] = row[1]
            
        # 针对 GWEI 等僵尸死池引入热门池子保底种子 (包含 0x473e34fa...)
        pools_seeds = ["0x473e34fad874524a146022cfd7c9df2af73988adeea0f21629dbb8c301305a17"]
        conn.close()

        meta_map = {}
        if os.path.exists(self.ai_sum_db_path):
            conn_sum = sqlite3.connect(self.ai_sum_db_path)
            cur_sum = conn_sum.cursor()
            try:
                cur_sum.execute("SELECT token_address, meta_score, meta_verdict, stage, whale_level FROM meta_snapshots ORDER BY scan_time DESC")
                for row in cur_sum.fetchall():
                    addr = row[0].lower()
                    if addr not in meta_map:
                        meta_map[addr] = {
                            "meta_score": row[1], "meta_verdict": row[2],
                            "stage": row[3], "whale_level": row[4]
                        }
            except Exception:
                pass
            conn_sum.close()

        return tokens, pools_seeds, token_symbol_map, meta_map

    def parse_single_pool_data(self, p, token_symbol_map, meta_map):
        p_addr = p.get("id", "").split("_")[-1]
        attr = p.get("attributes", {})
        rel = p.get("relationships", {})
        dex_id = rel.get("dex", {}).get("data", {}).get("id", "").lower()
        raw_reserve = float(attr.get("reserve_in_usd", 0) or 0)
        pool_vol_24h = float(attr.get("volume_usd", {}).get("h24", 0) or 0)
        b_id = clean_addr(rel.get("base_token", {}).get("data", {}).get("id", ""))
        q_id = clean_addr(rel.get("quote_token", {}).get("data", {}).get("id", ""))
        token_price = float(attr.get("base_token_price_usd", 0) or 0)
        quote_price = float(attr.get("quote_token_price_usd", 1.0) or 1.0)

        is_clmm = ("infinity" in dex_id or "clmm" in dex_id or "uniswap-v4" in dex_id or len(p_addr) == 66)
        is_stable = (b_id in self.STABLECOINS or q_id in self.STABLECOINS)

        if is_stable and raw_reserve >= LARGE_FAKE_THRESHOLD and is_clmm:
            active_tvl = round(raw_reserve * 0.117, 2)
            if "infinity" in dex_id or len(p_addr) == 66:
                raw_q = get_token_balance(q_id, self.CL_POOL_MANAGER)
                onchain_usd = (raw_q / 1e18) * quote_price
            else:
                raw_b = get_token_balance(b_id, p_addr)
                raw_q = get_token_balance(q_id, p_addr)
                onchain_usd = ((raw_b / 1e18) * token_price) + ((raw_q / 1e18) * quote_price)

            penalty = -40.0 if pool_vol_24h < 50.0 else 0.0
            addr = b_id if b_id not in self.STABLECOINS else q_id
            sym = token_symbol_map.get(addr.lower(), "N/A")
            meta_info = meta_map.get(addr.lower(), {"meta_score": None, "meta_verdict": "N/A", "stage": "N/A", "whale_level": "N/A"})

            return {
                "token": addr, "symbol": sym, "status": "FAKE_ZERO_LIQUIDITY",
                "fake_pair_name": attr.get("name", ""), "pool_id": p_addr,
                "raw_fake_val": raw_reserve, "active_tvl": active_tvl,
                "vol_h24": pool_vol_24h, "onchain_usd": onchain_usd, "penalty": penalty,
                "meta_score": meta_info["meta_score"], "meta_verdict": meta_info["meta_verdict"],
                "stage": meta_info["stage"], "whale_level": meta_info["whale_level"]
            }
        return None

    def analyze(self):
        tokens, pools_seeds, token_symbol_map, meta_map = self.load_tokens_pools_and_meta()
        token_results = []
        seen_pools = set()

        # 通道 A: 代币盲扫通道
        def process_token_pools(item):
            addr, chain = item
            net = chain if chain in ['bsc', 'eth', 'base', 'solana'] else 'bsc'
            url = f"https://api.geckoterminal.com/api/v2/networks/{net}/tokens/{addr}/pools"
            res = fetch_json(url)
            pool_results = []
            if res and "data" in res:
                for p in res["data"]:
                    parsed = self.parse_single_pool_data(p, token_symbol_map, meta_map)
                    if parsed: pool_results.append(parsed)
            return pool_results

        # 通道 B: 热门池子直连穿透通道 (Pool-Direct Query 抢救保底)
        def process_pool_direct(pool_address):
            url = f"https://api.geckoterminal.com/api/v2/networks/bsc/pools/{pool_address}"
            res = fetch_json(url)
            if res and "data" in res:
                parsed = self.parse_single_pool_data(res["data"], token_symbol_map, meta_map)
                return [parsed] if parsed else []
            return []

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures_a = [executor.submit(process_token_pools, t) for t in tokens]
            futures_b = [executor.submit(process_pool_direct, p_addr) for p_addr in pools_seeds]
            
            for f in as_completed(futures_a + futures_b):
                try:
                    r = f.result()
                    if r:
                        for item in r:
                            if item["pool_id"] not in seen_pools:
                                seen_pools.add(item["pool_id"])
                                token_results.append(item)
                except Exception:
                    pass

        return token_results
