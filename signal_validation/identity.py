from __future__ import annotations
import sqlite3
import hashlib
from collections import defaultdict
from datetime import datetime, timedelta
from signal_validation.source_loader import SourceLoader

def d(x):
    return datetime.fromisoformat(str(x).replace('Z', '+00:00').replace('+00:00', ''))

def audit_and_bind_identity(sum_db: str, select_db: str) -> list[dict]:
    s = sqlite3.connect(f'file:{sum_db}?mode=ro', uri=True)
    s.row_factory = sqlite3.Row
    
    # 统一使用 SourceLoader 进行连接与 P0 审计
    loader = SourceLoader(select_db)
    loader.execute_audit_p0()
    q = loader.connect()
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
        key = (r['chain'], str(r['token_address']).lower())
        by[key].append(r)
        
    a_events = []
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

    # D6: 基于 SHA-256 计算防漂移的稳定 event_id
    for ev in a_events:
        pool_str = ev['pool_address'] or 'N/A'
        at_str = ev['at'].isoformat(sep=' ') if isinstance(ev['at'], datetime) else str(ev['at'])
        raw_str = f"{ev['chain']}|{ev['address']}|{pool_str}|{at_str}|{ev['stage']}"
        ev['event_id'] = hashlib.sha256(raw_str.encode('utf-8')).hexdigest()[:16]

    s.close()
    loader.close()
    return a_events

