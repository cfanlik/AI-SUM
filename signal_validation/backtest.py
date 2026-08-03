from __future__ import annotations
import csv
import json
import sqlite3
import hashlib
import os
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from signal_validation.label import evaluate_single_asset_path, d, wilson_lower_bound
from signal_validation.source_loader import SourceLoader

def run_purged_walk_forward_backtest(
    sum_db: str,
    select_db: str,
    out_db: str,
    split_audit_csv_path: str = '/tmp/0802/test_run/split_audit.csv',
    git_commit: str = 'NOT_EVALUATED'
) -> dict:
    conn = sqlite3.connect(out_db)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    try:
        c.execute('ALTER TABLE asset_identity ADD COLUMN identity_conflict INTEGER DEFAULT 0')
        c.execute('ALTER TABLE asset_identity ADD COLUMN conflict_time TEXT')
        conn.commit()
    except sqlite3.OperationalError:
        pass
        
    identities = c.execute(
        'SELECT * FROM asset_identity WHERE identity_pass=1'
    ).fetchall()
    
    loader = SourceLoader(select_db)
    loader.execute_audit_p0()
    
    events = []
    for idt in identities:
        snaps = c.execute('''
            SELECT * FROM daily_feature_snapshot 
            WHERE chain=? AND token_address=? AND pool_address=?
        ''', (idt['chain'], idt['token_address'], idt['pool_address'])).fetchall()
        
        # 将 Row 转为 dict 以免 get 报错
        idt_dict = dict(idt)
        
        events.append({
            'chain': idt_dict['chain'],
            'address': idt_dict['token_address'],
            'pool_address': idt_dict['pool_address'],
            'symbol': idt_dict['token_symbol'],
            'at': d(idt_dict['a_time']),
            'event_id': idt_dict.get('event_id') or hashlib.sha256(f"{idt_dict['chain']}|{idt_dict['token_address']}|{idt_dict['pool_address']}|{idt_dict['a_time']}".encode()).hexdigest()[:16],
            'candidate_pools': json.loads(idt_dict['candidate_pools'] or '[]'),
            'identity_conflict': bool(idt_dict['identity_conflict'] if 'identity_conflict' in idt_dict else False),
            'conflict_time': d(idt_dict['conflict_time']) if ('conflict_time' in idt_dict and idt_dict['conflict_time']) else None,
            'snapshots': [dict(s) for s in snaps]
        })
        
    events.sort(key=lambda x: x['at'])
    
    if not events:
        print("ERROR: 无可用 A 事件参与回测。")
        conn.close()
        return {}
        
    split_pct = 0.80
    split_idx = int(len(events) * split_pct)
    split_date = events[split_idx]['at']
    
    train_events_raw = [ev for ev in events if ev['at'] < split_date]
    holdout_events_raw = [ev for ev in events if ev['at'] >= split_date]
    
    embargo_limit = split_date - timedelta(days=37)
    train_eligible_events = [ev for ev in train_events_raw if ev['at'] < embargo_limit]
    train_eligible_count = len(train_eligible_events)
    
    eligible_identities = len(set((ev['chain'], ev['address'], ev['pool_address']) for ev in train_eligible_events))
    
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
                ev['at'], ev['snapshots'], ev['identity_conflict'], ev['conflict_time'],
                g['v2_mul'], g['v3_mul'], g['breakout'], loader
            )
            records.append({**ev, **res})
            
        purged_records = [r for r in records if d(r['label_end_time']) < embargo_limit]
        b_trig = [r for r in purged_records if r['path_label'] == 'B_triggered' and r['outcome_incomplete'] == 'PASS']
        
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
        
        unique_identities_train_b = len(set((r['chain'], r['address'], r['pool_address']) for r in b_trig))
        
        if unique_identities_train_b >= 30:
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

    status = 'SUCCESS'
    if best_grid is None or eligible_identities < 30:
        status = 'INSUFFICIENT_TRAINING_SAMPLE'
        best_grid = {'name': 'NOT_RUN', 'v2_mul': 0.0, 'v3_mul': 0.0, 'breakout': 0.0}
        
    print(f"训练网格选择完毕: {best_grid['name']} | 状态: {status}")

    holdout_records = []
    for ev in holdout_events_raw:
        if best_grid['name'] == 'NOT_RUN':
            res = {
                'path_label': 'censored', 'path_time': None, 'label_end_time': ev['at'].isoformat(sep=' '),
                'reason': 'INSUFFICIENT_TRAINING_SAMPLE', 'r1d': None, 'r3d': None, 'r7d': None, 'mdd_7d': None,
                'outcome_incomplete': 'outcome_incomplete', 'soft_risk': 'PASS'
            }
        else:
            res = evaluate_single_asset_path(
                ev['chain'], ev['address'], ev['pool_address'], ev['candidate_pools'],
                ev['at'], ev['snapshots'], ev['identity_conflict'], ev['conflict_time'],
                best_grid['v2_mul'], best_grid['v3_mul'], best_grid['breakout'], loader
            )
        holdout_records.append({**ev, **res, 'split_type': 'Time_Holdout'})
        
    train_identities = set((ev['chain'], ev['address'], ev['pool_address']) for ev in train_events_raw)
    unseen_records = [r for r in holdout_records if (r['chain'], r['address'], r['pool_address']) not in train_identities]
    
    train_final_records = []
    for ev in train_events_raw:
        if best_grid['name'] == 'NOT_RUN':
            res = {
                'path_label': 'censored', 'path_time': None, 'label_end_time': ev['at'].isoformat(sep=' '),
                'reason': 'INSUFFICIENT_TRAINING_SAMPLE', 'r1d': None, 'r3d': None, 'r7d': None, 'mdd_7d': None,
                'outcome_incomplete': 'outcome_incomplete', 'soft_risk': 'PASS'
            }
        else:
            res = evaluate_single_asset_path(
                ev['chain'], ev['address'], ev['pool_address'], ev['candidate_pools'],
                ev['at'], ev['snapshots'], ev['identity_conflict'], ev['conflict_time'],
                best_grid['v2_mul'], best_grid['v3_mul'], best_grid['breakout'], loader
            )
        train_final_records.append({**ev, **res, 'split_type': 'Train'})

    def med_val(x):
        x = sorted(x)
        return x[len(x)//2] if len(x)%2 else (x[len(x)//2-1] + x[len(x)//2])/2

    def compute_oos_metrics(recs, is_holdout=False):
        b_trig = [r for r in recs if r['path_label'] == 'B_triggered' and r['outcome_incomplete'] == 'PASS']
        unique_ids = len(set((r['chain'], r['address'], r['pool_address']) for r in b_trig))
        
        has_enough = (unique_ids >= 30) and (status == 'SUCCESS')
        
        r1d = [r['r1d'] for r in b_trig if r['r1d'] is not None]
        r7d = [r['r7d'] for r in b_trig if r['r7d'] is not None]
        mdd = [r['mdd_7d'] for r in b_trig if r['mdd_7d'] is not None]
        
        expectancy = sum(r7d)/len(r7d) if (r7d and has_enough) else None
        wilson = wilson_lower_bound(sum(1 for x in r1d if x > 0), len(r1d)) if (r1d and has_enough) else None
        mdd_median = med_val(mdd) if (mdd and has_enough) else None
        
        mdd_worst_90 = None
        if mdd and has_enough:
            sorted_mdd = sorted(mdd)
            mdd_worst_90 = sorted_mdd[int(len(sorted_mdd)*0.10)]
            
        return {
            'total_events': len(recs),
            'unique_ids': unique_ids,
            'b_count': len(b_trig),
            'has_enough': has_enough,
            'expectancy': expectancy,
            'wilson_lower': wilson,
            'mdd_median': mdd_median,
            'mdd_worst_90': mdd_worst_90
        }

    time_metrics = compute_oos_metrics(holdout_records, is_holdout=True)
    unseen_metrics = compute_oos_metrics(unseen_records)
    
    if status == 'SUCCESS' and (not time_metrics['has_enough'] or not unseen_metrics['has_enough']):
        status = 'INSUFFICIENT_OOS_SAMPLE'
    
    config_data = {
        'grids': GRIDS,
        'embargo_days': 37,
        'split_pct': 0.80,
        'min_train_samples': 30,
        'best_grid_name': best_grid['name'],
        'status': status
    }
    config_json = json.dumps(config_data, sort_keys=True)
    config_hash = hashlib.sha256(config_json.encode('utf-8')).hexdigest()[:16]
    
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
                label_end_time, reason, r1d, r3d, r7d, mdd_7d, outcome_incomplete, soft_risk, split_type, overlap_check
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            r['chain'], r['address'], r['pool_address'], r['symbol'], r['at'].isoformat(sep=' '),
            r['path_label'], r['path_time'], r['label_end_time'], r['reason'],
            r['r1d'], r['r3d'], r['r7d'], r['mdd_7d'], r['outcome_incomplete'], r['soft_risk'], r['split_type'], overlap
        ))
        
    out_table_str = "".join(sorted([r['address'] for r in holdout_records]))
    output_table_hash = hashlib.sha256(out_table_str.encode()).hexdigest()[:16]
    
    try:
        mtime_epoch = os.path.getmtime(select_db)
        source_db_mtime = datetime.fromtimestamp(mtime_epoch).isoformat()
    except Exception:
        source_db_mtime = datetime.now().isoformat()
        
    c.execute('DELETE FROM run_manifest')
    c.execute('''
        INSERT INTO run_manifest (
            git_commit, schema_version, config_hash, grid_name, status,
            train_eligible_count, source_db_mtime, output_table_hash, run_time
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        git_commit, 'v6.0', config_hash, best_grid['name'], status,
        train_eligible_count, source_db_mtime, output_table_hash, datetime.now().isoformat()
    ))
    
    conn.commit()
    conn.close()
    loader.close()
    
    csv_file = Path(split_audit_csv_path)
    csv_file.parent.mkdir(parents=True, exist_ok=True)
    with csv_file.open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['event_id', 'symbol', 'chain', 'token_address', 'pool_address', 'a_time', 'label_end_time', 'path_label', 'split_type', 'overlap_check'])
        for r in all_final_recs:
            split_type = r['split_type']
            overlap = 'PASS'
            if split_type == 'Time_Holdout' and d(r['label_end_time']) < split_date:
                overlap = 'OVERLAP_VIOLATION'
            w.writerow([r['event_id'], r['symbol'], r['chain'], r['address'], r['pool_address'], r['at'].isoformat(sep=' '), r['label_end_time'], r['path_label'], split_type, overlap])
            
    print(f"信号验证明细已成功导出至 {split_audit_csv_path}")
    
    train_b_trig = [r for r in train_final_records if r['path_label'] == 'B_triggered' and r['outcome_incomplete'] == 'PASS']
    train_b_ids = len(set((r['chain'], r['address'], r['pool_address']) for r in train_b_trig))
    
    time_oos_b_trig = [r for r in holdout_records if r['path_label'] == 'B_triggered' and r['outcome_incomplete'] == 'PASS']
    time_oos_b_ids = len(set((r['chain'], r['address'], r['pool_address']) for r in time_oos_b_trig))
    
    unseen_oos_b_trig = [r for r in unseen_records if r['path_label'] == 'B_triggered' and r['outcome_incomplete'] == 'PASS']
    unseen_oos_b_ids = len(set((r['chain'], r['address'], r['pool_address']) for r in unseen_oos_b_trig))
    
    return {
        'config_hash': config_hash,
        'grid_name': best_grid['name'],
        'status': status,
        'train_eligible_count': train_eligible_count,
        'time_metrics': time_metrics,
        'unseen_metrics': unseen_metrics,
        'train_results': train_results_map,
        'total_written': len(all_final_recs),
        'independent_stats': {
            'eligible_events_ids': eligible_identities,
            'train_b_ids': train_b_ids,
            'time_oos_b_ids': time_oos_b_ids,
            'unseen_oos_b_ids': unseen_oos_b_ids
        }
    }

