from __future__ import annotations
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta

def d(x):
    return datetime.fromisoformat(str(x).replace('Z', '+00:00').replace('+00:00', ''))

def audit_and_bind_identity(sum_db: str, select_db: str) -> list[dict]:
    s = sqlite3.connect(f'file:{sum_db}?mode=ro', uri=True)
    s.row_factory = sqlite3.Row
    q = sqlite3.connect(f'file:{select_db}?mode=ro', uri=True)
    q.row_factory = sqlite3.Row

    # 1. 提取 A 原始事件
    TARGET = {'ACC_ACCELERATING', 'CONTROLLED'}
    meta = s.execute(
        'SELECT scan_time, chain, token_address, token_symbol, stage, engine_hits '
        'FROM meta_snapshots '
        'WHERE token_address IS NOT NULL '
        'ORDER BY token_address, scan_time'
    ).fetchall()
    
    by = defaultdict(list)
    for r in meta:
        by[str(r['token_address']).lower()].append(r)
        
    a_events = []
    for addr, rows in by.items():
        prev = ''
        last = None
        for r in rows:
            when = d(r['scan_time'])
            stage = str(r['stage'] or '')
            if stage in TARGET and prev not in TARGET and (not last or when - last >= timedelta(days=7)):
                chain = r['chain']
                confidence = 'high'
                identity_pass = True
                reason_code = 'PASS'
                
                if not chain or str(chain).strip() == '':
                    identity_pass = False
                    reason_code = 'chain_source_unavailable'
                    confidence = 'fail'
                
                a_events.append({
                    'chain': chain,
                    'address': addr,
                    'symbol': r['token_symbol'] or addr[:10],
                    'at': when,
                    'stage': stage,
                    'hits': int(r['engine_hits'] or 0),
                    'confidence': confidence,
                    'identity_pass': identity_pass,
                    'reason_code': reason_code,
                    'pool_address': None,
                    'candidate_pools': []
                })
                last = when
            prev = stage
            
    # 2. 主池前置绑定审计
    for ev in a_events:
        if not ev['identity_pass']:
            continue
            
        chain = ev['chain']
        addr = ev['address']
        at = ev['at']
        
        t_minus_24 = (at - timedelta(hours=24)).isoformat(sep=' ')
        t_str = at.isoformat(sep=' ')
        
        candidate_pools = q.execute('''
            SELECT DISTINCT pool_address, reserve_usd 
            FROM gecko_market_data 
            WHERE chain=? AND token_address=? AND scan_time>=? AND scan_time<=? 
            ORDER BY reserve_usd DESC
        ''', (chain, addr, t_minus_24, t_str)).fetchall()
        
        if not candidate_pools or float(candidate_pools[0]['reserve_usd'] or 0) == 0:
            ev['identity_pass'] = False
            ev['reason_code'] = 'pool_missing_before_a'
            continue
            
        ev['pool_address'] = candidate_pools[0]['pool_address']
        ev['candidate_pools'] = [p['pool_address'] for p in candidate_pools]

    s.close()
    q.close()
    return a_events
