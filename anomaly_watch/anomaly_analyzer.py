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
import urllib.error
from anomaly_watch.config import DB_PATH, AI_SUM_DB_PATH, MICRO_POOL_THRESHOLD, LARGE_FAKE_THRESHOLD, CORE_ASSETS, STABLECOINS, GAS_TOKENS, CL_POOL_MANAGER, RPC_ENDPOINTS, POOLS_SEEDS, ACTIVE_TVL_FACTOR, PENALTY_SCORE, ONCHAIN_FAKE_LIMIT, LEGAL_QUOTE_ASSETS

def clean_addr(s):
    if not s: return ""
    s = str(s).strip().lower()
    if "_" in s: s = s.split("_")[-1]
    return s

def fetch_json(url, max_retries=4):
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
        except urllib.error.HTTPError as e:
            time.sleep(0.3 * (attempt + 1))
        except Exception:
            time.sleep(0.2 * (attempt + 1))
    return None

def rpc_call(method, params):
    headers = {'Content-Type': 'application/json'}
    data = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode('utf-8')
    
    for rpc_url in RPC_ENDPOINTS:
        for attempt in range(2):
            p_url = get_proxy()
            proxy_handler = urllib.request.ProxyHandler({'http': p_url, 'https': p_url}) if p_url else urllib.request.BaseHandler()
            opener = urllib.request.build_opener(proxy_handler)
            try:
                req = urllib.request.Request(rpc_url, data=data, headers=headers)
                with opener.open(req, timeout=4) as resp:
                    res = json.loads(resp.read().decode('utf-8'))
                    if res and "result" in res:
                        return res
            except Exception:
                time.sleep(0.1)
                continue
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
    def __init__(self):
        self.db_path = DB_PATH
        self.ai_sum_db_path = AI_SUM_DB_PATH
        self.stablecoins = STABLECOINS
        self.gas_tokens = GAS_TOKENS
        self.cl_pool_manager = CL_POOL_MANAGER

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
            
        pools_seeds = POOLS_SEEDS
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

    def parse_single_pool_data(self, p, token_symbol_map, meta_map, all_pools=None):
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
        is_legal_pair = (b_id in LEGAL_QUOTE_ASSETS or q_id in LEGAL_QUOTE_ASSETS)

        # 核心物理风控门禁：CLMM/V4 且 标称Reserve >= $10万 且 包含稳定币或BNB/WBNB等主链Gas合法对
        if is_clmm and (raw_reserve >= LARGE_FAKE_THRESHOLD) and is_legal_pair:
            # 维 1 直接展现 API 原始名义价值（如 Pancake 官网 1.3M 挂单假象）
            active_tvl = round(raw_reserve, 2)
            
            # 核心物理单池独立解算：仅对当前物理池合约检索真实托管本金，彻底隔离多池污染
            onchain_usd = 0.0
            cur_p_addr = p_addr
            if "infinity" in dex_id or len(cur_p_addr) == 66:
                raw_q = get_token_balance(q_id, CL_POOL_MANAGER)
                val_q = (raw_q / 1e18) * quote_price
                if val_q > 0: onchain_usd += val_q
            else:
                raw_b = get_token_balance(b_id, cur_p_addr)
                raw_q = get_token_balance(q_id, cur_p_addr)
                val_b = (raw_b / 1e18) * token_price
                val_q = (raw_q / 1e18) * quote_price
                onchain_usd += (val_b + val_q)

            # 核心诱多防误杀断言：维 3 链上实测托管本金必须小等于 $10,000 美元
            if onchain_usd < ONCHAIN_FAKE_LIMIT:
                penalty = PENALTY_SCORE if pool_vol_24h < 50.0 else 0.0
                addr = b_id if b_id not in LEGAL_QUOTE_ASSETS else q_id
                sym = token_symbol_map.get(addr.lower(), "N/A")
                meta_info = meta_map.get(addr.lower(), {"meta_score": None, "meta_verdict": "N/A", "stage": "N/A", "whale_level": "N/A"})

                return {
                    "token": addr, "symbol": sym, "status": "FAKE_ZERO_LIQUIDITY",
                    "fake_pair_name": attr.get("name", ""), "pool_id": p_addr,
                    "raw_fake_val": raw_reserve, "active_tvl": active_tvl,
                    "vol_h24": pool_vol_24h, "onchain_usd": round(onchain_usd, 2), "penalty": penalty,
                    "meta_score": meta_info["meta_score"], "meta_verdict": meta_info["meta_verdict"],
                    "stage": meta_info["stage"], "whale_level": meta_info["whale_level"]
                }
        return None

    def analyze(self):
        tokens, pools_seeds, token_symbol_map, meta_map = self.load_tokens_pools_and_meta()
        token_results = []
        seen_pools = set()

        def process_token_pools(item):
            addr, chain = item
            net = chain if chain in ['bsc', 'eth', 'base', 'solana'] else 'bsc'
            url = f"https://api.geckoterminal.com/api/v2/networks/{net}/tokens/{addr}/pools"
            res = fetch_json(url)
            pool_results = []
            if res and "data" in res:
                all_p = res["data"]
                for p in all_p:
                    parsed = self.parse_single_pool_data(p, token_symbol_map, meta_map, all_pools=all_p)
                    if parsed: pool_results.append(parsed)
            return pool_results

        def process_pool_direct(pool_address):
            url = f"https://api.geckoterminal.com/api/v2/networks/bsc/pools/{pool_address}"
            res = fetch_json(url)
            if res and "data" in res:
                parsed = self.parse_single_pool_data(res["data"], token_symbol_map, meta_map, all_pools=[res["data"]])
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
