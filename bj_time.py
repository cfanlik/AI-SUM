"""AI-SUM 全局唯一北京时间 (Asia/Shanghai, UTC+8) 工具模块。"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    BJ_TZ = ZoneInfo("Asia/Shanghai")
except Exception:
    BJ_TZ = timezone(timedelta(hours=8))


def ensure_beijing_tz() -> None:
    if os.environ.get("TZ") != "Asia/Shanghai":
        os.environ["TZ"] = "Asia/Shanghai"
    if hasattr(time, "tzset"):
        try:
            time.tzset()
        except Exception:
            pass


def now_bj(aware: bool = False) -> datetime:
    dt = datetime.now(BJ_TZ)
    return dt if aware else dt.replace(tzinfo=None)


def now_bj_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return now_bj().strftime(fmt)


def now_bj_iso(aware: bool = False) -> str:
    return now_bj(aware=aware).isoformat()
