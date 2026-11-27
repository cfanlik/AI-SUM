import os as _os, sys as _sys
_AS_ROOT = _os.path.dirname(_os.path.abspath(__file__))
if _os.path.basename(_AS_ROOT) != 'AI-SUM' and _os.path.exists(_os.path.join(_os.path.dirname(_AS_ROOT), 'bj_time.py')):
    _AS_ROOT = _os.path.dirname(_AS_ROOT)
if _AS_ROOT not in _sys.path:
    _sys.path.insert(0, _AS_ROOT)
from bj_time import ensure_beijing_tz as _ensure_bj_tz
_ensure_bj_tz()
import sys
import os

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(__file__))

from engine import run_full_scan, show_watchlist

if __name__ == "__main__":
    result = run_full_scan(use_cache=True, verbose=True, save_report=True)
