import sqlite3
import urllib.request
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from anomaly_watch.config import DB_PATH, AI_SUM_DB_PATH, MICRO_POOL_THRESHOLD, CORE_ASSETS

def get_env_proxy():
    env_path = "/opt/select-coin/.env"
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                if line.startswith("PROXY_URL="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        if val.startswith("socks://"): val = val.replace("socks://", "socks5://")
                        return val
    return None

PROXY_URL = get_env_proxy()

def clean_addr(s):
    if not s: return ""
    s = str(s).strip().lower()
    if "_" in s: s = s.split("_")[-1]
    return s

def fetch_json(url):
    headers = {'User-Agent': 'Mozilla/5.0'}
    proxy_handler = urllib.request.ProxyHandler({'http': PROXY_URL, 'https': PROXY_URL}) if PROXY_URL else urllib.request.BaseHandler()
    opener = urllib.request.build_opener(proxy_handler)
    req = urllib.request.Request(url, headers=headers)
    for retry in range(2):
        try:
            with opener.open(req, timeout=4) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception:
            time.sleep(0.2)
    return None

class AnomalyAnalyzer:
    def __init__(self):
        self.db_path = DB_PATH
        self.ai_sum_db_path = AI_SUM_DB_PATH

    def load_tokens(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT token_address, chain FROM token_scores")
        tokens = cursor.fetchall()
        
        token_symbol_map = {}
        cursor.execute("SELECT DISTINCT token_address, symbol FROM token_names")
        for row in cursor.fetchall():
            token_symbol_map[row[0].lower()] = row[1]
        conn.close()
        return tokens, token_symbol_map

    def analyze(self):
        tokens, token_symbol_map = self.load_tokens()
        raw_pools = []

        def fetch_token_pools(item):
            addr, chain = item
            net = chain if chain in ['bsc', 'eth', 'base', 'solana'] else 'bsc'
            url = f"https://api.geckoterminal.com/api/v2/networks/{net}/tokens/{addr}/pools"
            res = fetch_json(url)
            res_list = []
            if res and "data" in res:
                for p in res["data"]:
                    rel = p.get("relationships", {})
                    dex_id = rel.get("dex", {}).get("data", {}).get("id", "")
                    attr = p.get("attributes", {})
                    name = attr.get("name", "")
                    raw_reserve = float(attr.get("reserve_in_usd", 0) or 0)
                    p_addr = p.get("id", "").split("_")[-1]
                    b_id = clean_addr(rel.get("base_token", {}).get("data", {}).get("id", ""))
                    q_id = clean_addr(rel.get("quote_token", {}).get("data", {}).get("id", ""))
                    
                    tx_h24 = attr.get("transactions", {}).get("h24", {})
                    buys = int(tx_h24.get("buys", 0) or 0)
                    sells = int(tx_h24.get("sells", 0) or 0)
                    total_tx = buys + sells
                    vol_h24 = float(attr.get("volume_usd", {}).get("h24", 0) or 0)
                    
                    if "pancake" in dex_id.lower() and ("infinity" in dex_id.lower() or "clmm" in dex_id.lower() or len(p_addr) == 66):
                        sym = token_symbol_map.get(addr.lower(), "N/A")
                        res_list.append({
                            "token": addr, "symbol": sym, "dex_id": dex_id, "pool_id": p_addr,
                            "pair_name": name, "reserve_in_usd": raw_reserve, "net": net,
                            "base_token_id": b_id, "quote_token_id": q_id,
                            "total_tx": total_tx, "vol_h24": vol_h24
                        })
            return res_list

        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = [executor.submit(fetch_token_pools, t) for t in tokens]
            for f in as_completed(futures):
                res = f.result()
                if res: raw_pools.extend(res)

        sanitized = []
        for p in raw_pools:
            b_id = clean_addr(p["base_token_id"])
            q_id = clean_addr(p["quote_token_id"])
            t_id = clean_addr(p["token"])
            is_core = (b_id in CORE_ASSETS or q_id in CORE_ASSETS or t_id in CORE_ASSETS)
            raw_reserve = p["reserve_in_usd"]
            
            if not is_core:
                p["final_reserve"] = 0.0
                p["status"] = "NON_CORE_PAIR"
            elif p["total_tx"] > 0 or p["vol_h24"] > 0 or raw_reserve >= MICRO_POOL_THRESHOLD:
                if raw_reserve < MICRO_POOL_THRESHOLD:
                    p["final_reserve"] = raw_reserve
                    p["status"] = "MICRO_CORE_POOL"
                else:
                    p["final_reserve"] = raw_reserve
                    p["status"] = "VALID_LARGE_POOL"
            else:
                p["final_reserve"] = 0.0
                p["status"] = "DEAD_ZOMBIE_POOL"
            sanitized.append(p)
            
        return sanitized
