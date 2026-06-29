import os
import json

DB_PATH = os.getenv("DB_PATH", "/opt/select-coin/data/select.db")
AI_SUM_DB_PATH = os.getenv("AI_SUM_DB_PATH", "/opt/AI-SUM/select-sum.db")

MICRO_POOL_THRESHOLD = 5000.0
LARGE_FAKE_THRESHOLD = 100000.0
ACTIVE_TVL_FACTOR = 0.117
PENALTY_SCORE = -40.0

# 动态解析 JSON 资产配置文件
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "assets_config.json")
_config_data = {}
if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            _config_data = json.load(f)
    except Exception:
        pass

RPC_ENDPOINTS = _config_data.get("RPC_ENDPOINTS", ["https://bsc-dataseed.binance.org/"])
CL_POOL_MANAGER = _config_data.get("CL_POOL_MANAGER", "0xa0ffb9c1ce1fe56963b0321b32e7a0302114058b")
POOLS_SEEDS = _config_data.get("POOLS_SEEDS", ["0x473e34fad874524a146022cfd7c9df2af73988adeea0f21629dbb8c301305a17"])
STABLECOINS = set(_config_data.get("STABLECOINS", []))
GAS_TOKENS = set(_config_data.get("GAS_TOKENS", []))
CORE_ASSETS = set(_config_data.get("CORE_ASSETS", []))

