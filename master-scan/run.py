import sys
import os

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(__file__))

from engine import run_full_scan, show_watchlist

if __name__ == "__main__":
    result = run_full_scan(use_cache=False, verbose=True, save_report=True)
