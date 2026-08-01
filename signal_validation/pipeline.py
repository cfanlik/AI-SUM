from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from signal_validation.schema import init_db
from signal_validation.identity import audit_and_bind_identity, d
from signal_validation.resample import resample_daily_features

def execute_validation_pipeline(
    sum_db: str = '/opt/AI-SUM/select-sum.db',
    select_db: str = '/opt/select-coin/data/select.db',
    out_db: str = '/opt/AI-SUM/data/signal-validation.db'
):
    print("--- 开始 Launch Path 验证第一阶段 (P0-P2) ---")
    
    # 1. 初始化独立数据库
    init_db(out_db)
    
    conn = sqlite3.connect(out_db)
    c = conn.cursor()
    
    # 清空以支持重新运行 (P0-P2 审计重算幂等性)
    c.execute('DELETE FROM asset_identity')
    c.execute('DELETE FROM daily_feature_snapshot')
    
    # 2. 提取并核验 A 身份与主池
    events = audit_and_bind_identity(sum_db, select_db)
    
    identity_pass_count = 0
    coverage_pass_count = 0
    total_snapshots_saved = 0
    
    for ev in events:
        chain = ev['chain']
        addr = ev['address']
        at = ev['at']
        symbol = ev['symbol']
        stage = ev['stage']
        hits = ev['hits']
        confidence = ev['confidence']
        identity_pass = ev['identity_pass']
        reason_code = ev['reason_code']
        candidate_pools_json = json.dumps(ev.get('candidate_pools', []))
        
        pool_address = ev.get('pool_address')
        
        # 3. 对通过主池绑定的代币执行覆盖度与日终桶计算
        snapshots = []
        if identity_pass and pool_address:
            identity_pass_count += 1
            snapshots, coverage_pass, cov_reason = resample_daily_features(
                chain, addr, pool_address, at, select_db
            )
            if not coverage_pass:
                identity_pass = False
                reason_code = cov_reason
            else:
                coverage_pass_count += 1
                
        # 4. 写入 asset_identity 表
        c.execute('''
            INSERT INTO asset_identity (
                chain, token_address, pool_address, token_symbol, a_time, a_stage,
                engine_hits, chain_source, chain_confidence, identity_pass, reason_code, candidate_pools
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            chain, addr, pool_address, symbol, at.isoformat(sep=' '), stage,
            hits, chain, confidence, 1 if identity_pass else 0, reason_code, candidate_pools_json
        ))
        
        # 5. 写入 daily_feature_snapshot 表
        if identity_pass and snapshots:
            for s in snapshots:
                c.execute('''
                    INSERT INTO daily_feature_snapshot (
                        chain, token_address, pool_address, as_of_date, source_scan_time,
                        price_usd, reserve_usd, daily_24h_rolling_volume_proxy, dex_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    s['chain'], s['token_address'], s['pool_address'], s['as_of_date'], s['source_scan_time'],
                    s['price_usd'], s['reserve_usd'], s['daily_24h_rolling_volume_proxy'], s['dex_id']
                ))
            total_snapshots_saved += len(snapshots)

    conn.commit()
    conn.close()
    
    print(f"--- P0-P2 运行完毕 ---")
    print(f"提取 A 事件总数: {len(events)}")
    print(f"主池绑定通过数: {identity_pass_count}")
    print(f"自然日覆盖率审计通过数: {coverage_pass_count}")
    print(f"保存日桶特征快照数: {total_snapshots_saved}")
    
    return {
        'total_events': len(events),
        'identity_pass': identity_pass_count,
        'coverage_pass': coverage_pass_count,
        'saved_snapshots': total_snapshots_saved
    }

if __name__ == '__main__':
    execute_validation_pipeline()
