from __future__ import annotations
import sqlite3
import math
from collections import defaultdict
from datetime import datetime, timedelta

def d(x):
    return datetime.fromisoformat(str(x).replace('Z', '+00:00').replace('+00:00', ''))

def med(x):
    x = sorted(x)
    return x[len(x)//2] if len(x)%2 else (x[len(x)//2-1] + x[len(x)//2])/2

def is_v3_dex(dex_id):
    if not dex_id:
        return False
    did = str(dex_id).lower()
    return 'v3' in did or 'clmm' in did or 'slipstream' in did or 'fusion' in did or 'v4' in did

def wilson_lower_bound(pos, total, confidence=0.95):
    if total == 0:
        return 0.0
    z = 1.96 # 95% confidence
    phat = pos / total
    return (phat + z*z/(2*total) - z * math.sqrt((phat*(1-phat)+z*z/(4*total))/total)) / (1 + z*z/total)

def evaluate_single_asset_path(
    chain: str,
    token_address: str,
    frozen_pool: str,
    candidate_pools: list[str],
    a_time: datetime,
    daily_snapshots: list[dict],
    v2_mul: float,
    v3_mul: float,
    breakout: float,
    select_db_path: str
) -> dict:
    label = 'censored'
    bt = None
    reason = '30d 内未满足 B 或 I'
    label_end_time = a_time + timedelta(days=30)
    
    pts = []
    for s in sorted(daily_snapshots, key=lambda x: d(x['source_scan_time'])):
        pts.append((
            d(s['source_scan_time']),
            float(s['price_usd']),
            float(s['reserve_usd']),
            float(s['daily_24h_rolling_volume_proxy']),
            s['dex_id'],
            s['pool_address']
        ))
        
    split_idx = 0
    for idx, p in enumerate(pts):
        if p[0] >= a_time:
            split_idx = idx
            break
            
    for i in range(split_idx, len(pts)):
        ts, p, lp, v, dex_id, curr_pool = pts[i]
        
        window = pts[i-7:i]
        if len(window) < 7:
            continue
            
        bl = med([x[2] for x in window])
        bv = med([x[3] for x in window])
        
        lp_invalid = (lp <= bl * 0.60)
        
        identity_conflict = False
        if curr_pool != frozen_pool and curr_pool in candidate_pools:
            identity_conflict = True
            
        if lp_invalid or identity_conflict:
            label = 'I_invalidated'
            bt = ts
            reason_list = []
            if lp_invalid:
                reason_list.append('LP 相对前 7 日中位下降>=40%')
            if identity_conflict:
                reason_list.append('主池漂移 identity_conflict')
            reason = " + ".join(reason_list)
            label_end_time = ts
            break
            
        vol_mul = v3_mul if is_v3_dex(dex_id) else v2_mul
        b_vol = (v >= max(1.0, bv) * vol_mul)
        
        if p >= max(x[1] for x in window) * breakout and b_vol and lp >= bl * 0.90:
            label = 'B_triggered'
            bt = ts
            reason = f'7d 突破+成交量自适应({vol_mul}x)+LP未下降'
            label_end_time = ts + timedelta(days=7)
            break
            
    r1d, r3d, r7d = None, None, None
    mdd_7d = None
    soft_risk = 'PASS'
    
    if label == 'B_triggered' and bt is not None:
        times = [x[0] for x in pts]
        idx_entry = bisect_right_time(times, bt)
        if idx_entry < len(pts):
            entry_time = pts[idx_entry][0]
            entry_p = pts[idx_entry][1]
            
            obs_pts = [x for x in pts if entry_time <= x[0] <= entry_time + timedelta(days=7)]
            if obs_pts:
                p1d_snaps = [x for x in obs_pts if x[0] >= entry_time + timedelta(days=1)]
                p3d_snaps = [x for x in obs_pts if x[0] >= entry_time + timedelta(days=3)]
                p7d_snaps = [x for x in obs_pts if x[0] >= entry_time + timedelta(days=7)]
                
                r1d = round((p1d_snaps[0][1]/entry_p - 1)*100, 2) if p1d_snaps else None
                r3d = round((p3d_snaps[0][1]/entry_p - 1)*100, 2) if p3d_snaps else None
                r7d = round((p7d_snaps[0][1]/entry_p - 1)*100, 2) if p7d_snaps else None
                
                min_p = min(x[1] for x in obs_pts)
                mdd_7d = round((min_p/entry_p - 1)*100, 2)
                
        conn = sqlite3.connect(f'file:{select_db_path}?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        at_str = a_time.isoformat(sep=' ')
        bt_str = bt.isoformat(sep=' ')
        
        a_holders = c.execute('''
            SELECT snapshot_time, hold_percentage FROM bubblemap_holders
            WHERE chain=? AND token_address=? AND snapshot_time<=?
            ORDER BY snapshot_time DESC LIMIT 40
        ''', (chain, token_address, at_str)).fetchall()
        
        b_holders = c.execute('''
            SELECT snapshot_time, hold_percentage FROM bubblemap_holders
            WHERE chain=? AND token_address=? AND snapshot_time<=?
            ORDER BY snapshot_time DESC LIMIT 40
        ''', (chain, token_address, bt_str)).fetchall()
        
        conn.close()
        
        if a_holders and b_holders:
            a_gps = defaultdict(list)
            for r in a_holders: a_gps[r['snapshot_time']].append(float(r['hold_percentage'] or 0))
            b_gps = defaultdict(list)
            for r in b_holders: b_gps[r['snapshot_time']].append(float(r['hold_percentage'] or 0))
            
            a_ts = sorted(list(a_gps.keys()), reverse=True)[0]
            a_top10 = sum(sorted(a_gps[a_ts], reverse=True)[:10])
            
            b_ts = sorted(list(b_gps.keys()), reverse=True)[0]
            b_top10 = sum(sorted(b_gps[b_ts], reverse=True)[:10])
            
            if a_top10 - b_top10 >= 5.0:
                soft_risk = 'Top10_hold_net_variation_alert'

    return {
        'path_label': label,
        'path_time': bt.isoformat(sep=' ') if bt else None,
        'label_end_time': label_end_time.isoformat(sep=' '),
        'reason': reason,
        'r1d': r1d,
        'r3d': r3d,
        'r7d': r7d,
        'mdd_7d': mdd_7d,
        'soft_risk': soft_risk
    }

def bisect_right_time(times: list[datetime], target: datetime) -> int:
    import bisect
    ts_floats = [t.timestamp() for t in times]
    return bisect.bisect_right(ts_floats, target.timestamp())
