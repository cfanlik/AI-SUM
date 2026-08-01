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
    
    # C1: 状态机去重键使用复合键 (chain, token_address)
    by = defaultdict(list)
    for r in meta:
        key = (r['chain'], str(r['token_address']).lower())
        by[key].append(r)
        
    a_events = []
    # 冷却周期 7 天 (可配置)
    cooling_period = timedelta(days=7)
    
    for (chain, addr), rows in by.items():
        prev = ''
        last = None
        for r in rows:
            when = d(r['scan_time'])
            stage = str(r['stage'] or '')
            if stage in TARGET and prev not in TARGET:
                if not last or when - last >= cooling_period:
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
            
        c_chain = ev['chain']
        c_addr = ev['address']
        c_at = ev['at']
        
        t_minus_24 = (c_at - timedelta(hours=24)).isoformat(sep=' ')
        t_str = c_at.isoformat(sep=' ')
        
        # C2: GROUP BY pool_address 消除候选池中可能的重复，按 total_reserve 降序
        candidate_pools = q.execute('''
            SELECT pool_address, SUM(reserve_usd) as total_reserve
            FROM gecko_market_data
            WHERE chain=? AND token_address=? AND scan_time>=? AND scan_time<=?
            GROUP BY pool_address
            ORDER BY total_reserve DESC
        ''', (c_chain, c_addr, t_minus_24, t_str)).fetchall()
        
        if not candidate_pools or float(candidate_pools[0]['total_reserve'] or 0) == 0:
            ev['identity_pass'] = False
            ev['reason_code'] = 'pool_missing_before_a'
            continue
            
        ev['pool_address'] = candidate_pools[0]['pool_address']
        ev['candidate_pools'] = [p['pool_address'] for p in candidate_pools]

    s.close()
    q.close()
    return a_events
