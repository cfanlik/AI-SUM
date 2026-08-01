from __future__ import annotations
import csv
import json
import sqlite3
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from signal_validation.label import evaluate_single_asset_path, d, wilson_lower_bound

def run_purged_walk_forward_backtest(
    sum_db: str,
    select_db: str,
    out_db: str,
    split_audit_csv_path: str = '/tmp/0802/test_run/split_audit.csv'
) -> dict:
    conn = sqlite3.connect(out_db)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    identities = c.execute(
        'SELECT * FROM asset_identity WHERE identity_pass=1'
    ).fetchall()
    
    events = []
    for idt in identities:
        snaps = c.execute('''
            SELECT * FROM daily_feature_snapshot 
            WHERE chain=? AND token_address=? AND pool_address=?
        ''', (idt['chain'], idt['token_address'], idt['pool_address'])).fetchall()
        
        events.append({
            'chain': idt['chain'],
            'address': idt['token_address'],
            'pool_address': idt['pool_address'],
            'symbol': idt['token_symbol'],
            'at': d(idt['a_time']),
            'candidate_pools': json.loads(idt['candidate_pools'] or '[]'),
            'snapshots': [dict(s) for s in snaps]
        })
        
    events.sort(key=lambda x: x['at'])
    
    if not events:
        print("ERROR: 无可用 A 事件参与回测。")
        conn.close()
        return {}
        
    split_idx = int(len(events) * 0.8)
    split_date = events[split_idx]['at']
    
    train_events_raw = [ev for ev in events if ev['at'] < split_date]
    holdout_events_raw = [ev for ev in events if ev['at'] >= split_date]
    
    embargo_limit = split_date - timedelta(days=37)
    train_eligible_count = len([ev for ev in train_events_raw if ev['at'] < embargo_limit])
    
    GRIDS = [
        {'name': 'Grid_1_Conservative', 'v2_mul': 2.0, 'v3_mul': 4.0, 'breakout': 1.05},
        {'name': 'Grid_2_Standard', 'v2_mul': 1.5, 'v3_mul': 3.0, 'breakout': 1.02}
    ]
    
    best_grid = None
    best_expectancy = -999999.0
    best_wilson = -1.0
    best_triggered_count = 999999
    train_results_map = {}
    
    for g in GRIDS:
        records = []
        for ev in train_events_raw:
            res = evaluate_single_asset_path(
                ev['chain'], ev['address'], ev['pool_address'], ev['candidate_pools'],
                ev['at'], ev['snapshots'], g['v2_mul'], g['v3_mul'], g['breakout'], select_db
            )
            records.append({**ev, **res})
            
        purged_records = [r for r in records if d(r['label_end_time']) < embargo_limit]
        
        b_trig = [r for r in purged_records if r['path_label'] == 'B_triggered']
        r7d_vals = [r['r7d'] for r in b_trig if r['r7d'] is not None]
        expectancy = sum(r7d_vals)/len(r7d_vals) if r7d_vals else None
        
        r1d_vals = [r['r1d'] for r in b_trig if r['r1d'] is not None]
        wins = sum(1 for x in r1d_vals if x > 0)
        wilson = wilson_lower_bound(wins, len(r1d_vals)) if r1d_vals else 0.0
        
        triggered_count = len(b_trig)
        train_results_map[g['name']] = {
            'expectancy': expectancy,
            'wilson_lower': wilson,
            'triggered': triggered_count
        }
        
        if triggered_count >= 30:
            is_better = False
            if best_grid is None:
                is_better = True
            else:
                if expectancy > best_expectancy:
                    is_better = True
                elif abs(expectancy - best_expectancy) < 1e-5:
                    if wilson > best_wilson:
                        is_better = True
                    elif abs(wilson - best_wilson) < 1e-5:
                        if triggered_count < best_triggered_count:
                            is_better = True
            if is_better:
                best_expectancy = expectancy
                best_wilson = wilson
                best_triggered_count = triggered_count
                best_grid = g

    if best_grid is None or train_eligible_count < 30:
        status = 'INSUFFICIENT_TRAINING_SAMPLE'
        best_grid = GRIDS[0]
    else:
        status = 'SUCCESS'
        
    print(f"网格寻优完成，最优网格: {best_grid['name']}，状态: {status}")

    holdout_records = []
    for ev in holdout_events_raw:
        res = evaluate_single_asset_path(
            ev['chain'], ev['address'], ev['pool_address'], ev['candidate_pools'],
            ev['at'], ev['snapshots'], best_grid['v2_mul'], best_grid['v3_mul'], best_grid['breakout'], select_db
        )
        holdout_records.append({**ev, **res, 'split_type': 'Time_Holdout'})
        
    train_identities = set((ev['chain'], ev['address'], ev['pool_address']) for ev in train_events_raw)
    unseen_records = [r for r in holdout_records if (r['chain'], r['address'], r['pool_address']) not in train_identities]
    
    train_final_records = []
    for ev in train_events_raw:
        res = evaluate_single_asset_path(
            ev['chain'], ev['address'], ev['pool_address'], ev['candidate_pools'],
            ev['at'], ev['snapshots'], best_grid['v2_mul'], best_grid['v3_mul'], best_grid['breakout'], select_db
        )
        train_final_records.append({**ev, **res, 'split_type': 'Train'})

    def compute_metrics(recs):
        b_trig = [r for r in recs if r['path_label'] == 'B_triggered']
        i_inv = [r for r in recs if r['path_label'] == 'I_invalidated']
        c_cens = [r for r in recs if r['path_label'] == 'censored']
        
        m1d = [r['r1d'] for r in b_trig if r['r1d'] is not None]
        m3d = [r['r3d'] for r in b_trig if r['r3d'] is not None]
        m7d = [r['r7d'] for r in b_trig if r['r7d'] is not None]
        mdd = [r['mdd_7d'] for r in b_trig if r['mdd_7d'] is not None]
        
        has_enough = (len(b_trig) >= 30)
        
        return {
            'total': len(recs),
            'b_count': len(b_trig),
            'i_count': len(i_inv),
            'c_count': len(c_cens),
            'has_enough': has_enough,
            'r1d_median': med(m1d) if (m1d and has_enough) else None,
            'r3d_median': med(m3d) if (m3d and has_enough) else None,
            'r7d_median': med(m7d) if (m7d and has_enough) else None,
            'r7d_mean': sum(m7d)/len(m7d) if (m7d and has_enough) else None,
            'r1d_win': sum(1 for x in m1d if x > 0)/len(m1d) if (m1d and has_enough) else None,
            'mdd_median': med(mdd) if (mdd and has_enough) else None
        }

    time_metrics = compute_metrics(holdout_records)
    unseen_metrics = compute_metrics(unseen_records)
    
    h_input = hashlib.sha256()
    h_input.update(best_grid['name'].encode())
    h_input.update(status.encode())
    config_hash = h_input.hexdigest()[:16]
    
    c.execute('DELETE FROM backtest_run_history')
    c.execute('DELETE FROM signal_decision_result')
    
    c.execute('''
        INSERT INTO backtest_run_history (
            config_hash, grid_name, v2_mul, v3_mul, breakout, status, train_eligible_count, run_time
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        config_hash, best_grid['name'], best_grid['v2_mul'], best_grid['v3_mul'],
        best_grid['breakout'], status, train_eligible_count, datetime.now().isoformat()
    ))
    
    all_final_recs = train_final_records + holdout_records
    for r in all_final_recs:
        overlap = 'PASS'
        if r['split_type'] == 'Time_Holdout' and d(r['label_end_time']) < split_date:
            overlap = 'OVERLAP_VIOLATION'
            
        c.execute('''
            INSERT INTO signal_decision_result (
                chain, token_address, pool_address, symbol, a_time, path_label, path_time,
                label_end_time, reason, r1d, r3d, r7d, mdd_7d, soft_risk, split_type, overlap_check
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            r['chain'], r['address'], r['pool_address'], r['symbol'], r['at'].isoformat(sep=' '),
            r['path_label'], r['path_time'], r['label_end_time'], r['reason'],
            r['r1d'], r['r3d'], r['r7d'], r['mdd_7d'], r['soft_risk'], r['split_type'], overlap
        ))
        
    conn.commit()
    conn.close()
    
    csv_file = Path(split_audit_csv_path)
    csv_file.parent.mkdir(parents=True, exist_ok=True)
    with csv_file.open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['event_id', 'symbol', 'chain', 'token_address', 'pool_address', 'a_time', 'label_end_time', 'path_label', 'split_type', 'overlap_check'])
        for idx, r in enumerate(holdout_records):
            overlap = 'PASS'
            if d(r['label_end_time']) < split_date:
                overlap = 'OVERLAP_VIOLATION'
            w.writerow([f"H_{idx}", r['symbol'], r['chain'], r['address'], r['pool_address'], r['at'].isoformat(sep=' '), r['label_end_time'], r['path_label'], r['split_type'], overlap])
            
    print(f"信号验证明细已成功导出至 {split_audit_csv_path}")
    
    return {
        'config_hash': config_hash,
        'grid_name': best_grid['name'],
        'status': status,
        'train_eligible_count': train_eligible_count,
        'time_metrics': time_metrics,
        'unseen_metrics': unseen_metrics,
        'train_results': train_results_map,
        'total_written': len(all_final_recs)
    }
