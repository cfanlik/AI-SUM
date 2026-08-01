from __future__ import annotations
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta

def d(x):
    return datetime.fromisoformat(str(x).replace('Z', '+00:00').replace('+00:00', ''))

def resample_daily_features(
    chain: str,
    token_address: str,
    pool_address: str,
    at: datetime,
    select_db: str
) -> tuple[list[dict], bool, str]:
    q = sqlite3.connect(f'file:{select_db}?mode=ro', uri=True)
    q.row_factory = sqlite3.Row
    
    # 覆盖度审计区间：A时点前 7d 到 A后 30d (一共 37d 范围)
    start_time = (at - timedelta(days=7)).isoformat(sep=' ')
    end_time = (at + timedelta(days=30)).isoformat(sep=' ')
    
    market_rows = q.execute('''
        SELECT scan_time, price_usd, reserve_usd, volume_24h, dex_id, pool_address
        FROM gecko_market_data
        WHERE chain=? AND token_address=? AND pool_address=?
          AND scan_time>=? AND scan_time<=?
        ORDER BY scan_time
    ''', (chain, token_address, pool_address, start_time, end_time)).fetchall()
    
    q.close()
    
    # 日终 UTC 自然日重采样
    day_snapshots = defaultdict(list)
    for mr in market_rows:
        dt = d(mr['scan_time'])
        day_snapshots[dt.date()].append(mr)
        
    daily_market = []
    for day, snaps in day_snapshots.items():
        snaps.sort(key=lambda x: d(x['scan_time']))
        daily_market.append(snaps[-1])
        
    daily_market.sort(key=lambda x: d(x['scan_time']))
    
    # 行情覆盖率审计
    actual_post_days = len([m for m in daily_market if d(m['scan_time']) >= at])
    coverage_pct = actual_post_days / 30.0
    
    max_gap_days = 0
    if daily_market:
        sorted_dates = sorted([d(m['scan_time']).date() for m in daily_market])
        for i in range(len(sorted_dates)-1):
            gap = (sorted_dates[i+1] - sorted_dates[i]).days - 1
            if gap > max_gap_days:
                max_gap_days = gap
                
    coverage_pass = True
    reason_code = 'PASS'
    if coverage_pct < 0.90:
        coverage_pass = False
        reason_code = 'insufficient_post_history_coverage'
    elif max_gap_days > 1:
        coverage_pass = False
        reason_code = 'history_gap_too_large'
        
    # 构建输出快照行
    snapshots = []
    for m in daily_market:
        mdt = d(m['scan_time'])
        snapshots.append({
            'chain': chain,
            'token_address': token_address,
            'pool_address': pool_address,
            'as_of_date': mdt.date().isoformat(),
            'source_scan_time': mdt.isoformat(sep=' '),
            'price_usd': float(m['price_usd']),
            'reserve_usd': float(m['reserve_usd']),
            'daily_24h_rolling_volume_proxy': float(m['volume_24h'] or 0),
            'dex_id': m['dex_id']
        })
        
    return snapshots, coverage_pass, reason_code
