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
            with opener.open(req, timeout=5) as resp:
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
        with opener.open(req, timeout=5) as resp:
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

    def load_tokens_and_meta(self):
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

        return tokens, token_symbol_map, meta_map

    def analyze(self):
        tokens, token_symbol_map, meta_map = self.load_tokens_and_meta()
        token_results = []

        def process_token_pools(item):
            addr, chain = item
            net = chain if chain in ['bsc', 'eth', 'base', 'solana'] else 'bsc'
            url = f"https://api.geckoterminal.com/api/v2/networks/{net}/tokens/{addr}/pools"
            res = fetch_json(url)
            
            meta_info = meta_map.get(addr.lower(), {"meta_score": None, "meta_verdict": "N/A", "stage": "N/A", "whale_level": "N/A"})
            sym = token_symbol_map.get(addr.lower(), "N/A")
            pool_results = []

            if res and "data" in res:
                for p in res["data"]:
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

                    # 池子物理粒度 (Pool-Level) 独立伪流动性判定：原始标称 >= $10万 && CLMM协议 && 该单池交易量 < $50.0
                    if is_stable and raw_reserve >= LARGE_FAKE_THRESHOLD and is_clmm:
                        active_tvl = round(raw_reserve * 0.117, 2)
                        
                        # 维 3 RPC 单池解算
                        if "infinity" in dex_id or len(p_addr) == 66:
                            raw_q = get_token_balance(q_id, self.CL_POOL_MANAGER)
                            onchain_usd = (raw_q / 1e18) * quote_price
                        else:
                            raw_b = get_token_balance(b_id, p_addr)
                            raw_q = get_token_balance(q_id, p_addr)
                            onchain_usd = ((raw_b / 1e18) * token_price) + ((raw_q / 1e18) * quote_price)

                        # 单池独立物理扣分：零换手死池强行扣 -40 分
                        penalty = -40.0 if pool_vol_24h < 50.0 else 0.0

                        pool_results.append({
                            "token": addr,
                            "symbol": sym,
                            "status": "FAKE_ZERO_LIQUIDITY",
                            "fake_pair_name": attr.get("name", ""),
                            "pool_id": p_addr,
                            "raw_fake_val": raw_reserve,
                            "active_tvl": active_tvl,
                            "vol_h24": pool_vol_24h,
                            "onchain_usd": onchain_usd,
                            "penalty": penalty,
                            "meta_score": meta_info["meta_score"],
                            "meta_verdict": meta_info["meta_verdict"],
                            "stage": meta_info["stage"],
                            "whale_level": meta_info["whale_level"]
                        })
            return pool_results

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(process_token_pools, t) for t in tokens]
            for f in as_completed(futures):
                r = f.result()
                if r: token_results.extend(r)

        return token_results
