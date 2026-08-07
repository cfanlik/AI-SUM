import sqlite3
import pandas as pd
import numpy as np
import time
from datetime import datetime, timedelta
import os
import uuid

# 数据库路径配置
SRC_DB = "/opt/select-coin/data/select.db"
VAL_DB = "/opt/AI-SUM/data/signal-validation.db"
REPORT_DIR = "/opt/AI-SUM/report/anomaly"

# 隔离环境下自适应为测试路径 (自愈逻辑)
if "/tmp/0805" in os.path.abspath(__file__):
    SRC_DB = "file:/opt/select-coin/data/select.db?mode=ro"
    VAL_DB = "/tmp/0805/signal-validation.db"
    REPORT_DIR = "/tmp/0805/report/anomaly"

def connect(path, readonly=False):
    uri = f"file:{path}?mode=ro" if readonly else path
    c = sqlite3.connect(uri, uri=readonly)
    c.row_factory = sqlite3.Row
    return c

def generate_report():
    t0 = time.time()
    
    if not os.path.exists(VAL_DB):
        print(f"Error: 隔离物化底座数据库不存在: {VAL_DB}")
        return
        
    val_conn = sqlite3.connect(VAL_DB)
    val_conn.row_factory = sqlite3.Row
    
    # A. 提取最新一次 run_manifest 信息
    try:
        manifest = dict(val_conn.execute("SELECT * FROM run_manifest ORDER BY as_of_utc DESC LIMIT 1").fetchone())
    except Exception as e:
        print(f"Error: 无法读取 run_manifest 记录: {e}")
        val_conn.close()
        return

    latest_price_time = val_conn.execute("SELECT MAX(scan_time) FROM market_pool_asof").fetchone()[0]

    # B. 异动扫描与双轨级联级判定 (Dual-Gate Cascade)
    df = pd.read_sql_query("SELECT * FROM snapshot_asset_metrics ORDER BY chain, token_address, snapshot_time", val_conn)
    
    # 2 快照变动
    df['prev_s1'] = df.groupby(['chain','token_address'])['avg_acc_score'].shift(1)
    df['prev_a1'] = df.groupby(['chain','token_address'])['acc_count'].shift(1)
    df['ds_2'] = df['avg_acc_score'] - df['prev_s1']
    df['da_2'] = df['acc_count'] - df['prev_a1']
    
    # 3 快照变动
    df['prev_s2'] = df.groupby(['chain','token_address'])['avg_acc_score'].shift(2)
    df['prev_a2'] = df.groupby(['chain','token_address'])['acc_count'].shift(2)
    df['ds_3'] = df['avg_acc_score'] - df['prev_s2']
    df['da_3'] = df['acc_count'] - df['prev_a2']
    
    df = df.dropna(subset=['prev_s1'])

    # 双轨级联机制
    cond_w2 = (df['ds_2'] >= 2.0) & (df['da_2'] >= 10)
    cond_s2 = (df['ds_2'] >= 5.0) & (df['da_2'] >= 50)
    
    cond_w3 = (df['ds_3'] >= 3.0) & (df['da_3'] >= 15)
    cond_s3 = (df['ds_3'] >= 6.0) & (df['da_3'] >= 60)
    
    df['lv'] = 'NONE'
    df.loc[cond_w2 | cond_w3, 'lv'] = 'WATCH'
    df.loc[cond_s2 | cond_s3, 'lv'] = 'WATCH_STRONG'
    
    # 筛选出触发异动的快照
    trig = df[df['lv'] != 'NONE'].copy()
    
    # C. 去噪优化：相同代币仅展示最近 2 期
    trig = trig.sort_values(by='snapshot_time', ascending=False)
    trig = trig.groupby(['chain', 'token_address']).head(2)

    # D. 结算对齐与数据格式化
    pr = pd.read_sql_query("SELECT * FROM market_pool_asof", val_conn)
    canon = pr.groupby(['chain','token_address'])['pool_address'].agg(lambda x: x.value_counts().index[0] if len(x)>0 else None).to_dict()
    ldt = pd.to_datetime(latest_price_time)
    
    out = []
    strong_signals = [] # 收集强异动快照，供后续动态名单审计

    for _, r in trig.iterrows():
        pool = canon.get((r['chain'], r['token_address']))
        ts = pd.to_datetime(r['snapshot_time'])

        entry_p = None
        if pool:
            sub = pr[(pr['chain'] == r['chain']) & (pr['token_address'] == r['token_address']) & (pr['pool_address'] == pool)].copy()
            sub['dt'] = pd.to_datetime(sub['scan_time'])
            sub['diff'] = (sub['dt'] - ts).abs()
            ep = sub.sort_values('diff').iloc[0] if len(sub) > 0 else None
            entry_p = ep['price_usd'] if ep is not None and ep['diff'] < timedelta(hours=12) else None

        es = ts + timedelta(days=3)
        ee = es + timedelta(hours=4)

        if es > ldt:
            status = 'PENDING_OUTCOME'
            ret = None
        elif not pool:
            status = 'POOL_UNRESOLVED'
            ret = None
        else:
            ex = sub[(sub['dt'] >= es) & (sub['dt'] <= ee)].sort_values('dt')
            if len(ex) > 0 and entry_p:
                status = 'SETTLED_POOL_ALIGNED'
                ret = (ex.iloc[0]['price_usd'] - entry_p) / entry_p * 100
            else:
                status = 'EXIT_SNAPSHOT_MISSING'
                ret = None

        # 增加 7d 收益结算报价窗口查询
        es_7d = ts + timedelta(days=7)
        ee_7d = es_7d + timedelta(hours=4)

        if es_7d > ldt:
            ret_7d = None
        else:
            ex_7d = sub[(sub['dt'] >= es_7d) & (sub['dt'] <= ee_7d)].sort_values('dt')
            if len(ex_7d) > 0 and entry_p:
                ret_7d = (ex_7d.iloc[0]['price_usd'] - entry_p) / entry_p * 100
            else:
                ret_7d = None

        t_type = []
        if (r['ds_2'] >= 2.0) and (r['da_2'] >= 10): t_type.append("2期")
        if (not pd.isna(r['ds_3'])) and (r['ds_3'] >= 3.0) and (r['da_3'] >= 15): t_type.append("3期")
        trigger_tag = "+".join(t_type) if t_type else "级联"

        out.append({
            'sym': r['symbol'],
            'time': r['snapshot_time'],
            'da': f"+{r['da_2']:.0f}" if not pd.isna(r['da_2']) else "—",
            'score': f"{r['avg_acc_score']:.2f}",
            'ds': f"+{r['ds_2']:.2f}" if not pd.isna(r['ds_2']) else "—",
            'status': status,
            'ret': f"{ret:+.2f}%" if ret is not None else '—',
            'ret_7d': f"{ret_7d:+.2f}%" if ret_7d is not None else '—',
            'lv': r['lv'],
            'pool': (pool[:10] + '..') if pool else 'N/A',
            'trig_tag': trigger_tag
        })
        
        if r['lv'] == 'WATCH_STRONG':
            strong_signals.append((r['chain'], r['token_address'], r['snapshot_time'], r['symbol'], r['prev_s1']))

    # E. 动态大户双快照差异审计 (消除任何代币硬编码)
    audit_sections = []
    for chain, addr, snap_new, symbol, prev_score in strong_signals:
        # 获取同一代币上一次的快照时间
        prev_snap_row = val_conn.execute("""
            SELECT snapshot_time FROM snapshot_asset_metrics
            WHERE chain=? AND token_address=? AND snapshot_time < ?
            ORDER BY snapshot_time DESC LIMIT 1
        """, [chain, addr, snap_new]).fetchone()
        
        if not prev_snap_row:
            continue
        snap_old = prev_snap_row[0]
        
        # 提取名单 (若隔离库中无缓存则动态按需补拉)
        w_new_rows = val_conn.execute(
            "SELECT * FROM snapshot_wallet_membership WHERE chain=? AND token_address=? AND snapshot_time=?",
            [chain, addr, snap_new]).fetchall()
        if not w_new_rows:
            src_conn = connect(SRC_DB, readonly=True)
            src_rows = src_conn.execute(
                "SELECT chain, token_address, snapshot_time, wallet_address, is_accumulating, acc_score, is_cex, is_dex, is_contract FROM bubblemap_holders WHERE token_address=? AND snapshot_time=?",
                [addr, snap_new]).fetchall()
            src_conn.close()
            val_conn.executemany(
                "INSERT OR REPLACE INTO snapshot_wallet_membership VALUES (?,?,?,?,?,?,?,?,?)",
                [list(r) for r in src_rows])
            val_conn.commit()
            w_new_rows = val_conn.execute(
                "SELECT * FROM snapshot_wallet_membership WHERE chain=? AND token_address=? AND snapshot_time=?",
                [chain, addr, snap_new]).fetchall()

        w_old_rows = val_conn.execute(
            "SELECT * FROM snapshot_wallet_membership WHERE chain=? AND token_address=? AND snapshot_time=?",
            [chain, addr, snap_old]).fetchall()
        if not w_old_rows:
            src_conn = connect(SRC_DB, readonly=True)
            src_rows = src_conn.execute(
                "SELECT chain, token_address, snapshot_time, wallet_address, is_accumulating, acc_score, is_cex, is_dex, is_contract FROM bubblemap_holders WHERE token_address=? AND snapshot_time=?",
                [addr, snap_old]).fetchall()
            src_conn.close()
            val_conn.executemany(
                "INSERT OR REPLACE INTO snapshot_wallet_membership VALUES (?,?,?,?,?,?,?,?,?)",
                [list(r) for r in src_rows])
            val_conn.commit()
            w_old_rows = val_conn.execute(
                "SELECT * FROM snapshot_wallet_membership WHERE chain=? AND token_address=? AND snapshot_time=?",
                [chain, addr, snap_old]).fetchall()

        w_new = {r['wallet_address']: dict(r) for r in w_new_rows}
        w_old = {r['wallet_address']: dict(r) for r in w_old_rows}

        s_new = dict(val_conn.execute("SELECT * FROM snapshot_asset_metrics WHERE chain=? AND token_address=? AND snapshot_time=?", [chain, addr, snap_new]).fetchone())
        s_old = dict(val_conn.execute("SELECT * FROM snapshot_asset_metrics WHERE chain=? AND token_address=? AND snapshot_time=?", [chain, addr, snap_old]).fetchone())

        inter = set(w_new) & set(w_old)
        entered = set(w_new) - set(w_old)
        exited = set(w_old) - set(w_new)
        
        min_len = min(len(w_new), len(w_old))
        turnover = 1.0 - len(inter) / min_len if min_len > 0 else 0.0
        entered_acc = sum(1 for w in entered if w_new[w]['is_accumulating'] == 1)
        entered_sys = sum(1 for w in entered if w_new[w]['is_cex'] == 1 or w_new[w]['is_dex'] == 1 or w_new[w]['is_contract'] == 1)
        entered_len = len(entered)
        
        acc_pct_str = f"{entered_acc}/{entered_len} ({entered_acc/entered_len:.1%})" if entered_len > 0 else "0/0 (0.0%)"
        sys_pct_str = f"{entered_sys}/{entered_len}" if entered_len > 0 else "0/0"

        audit_md = f"""
### {symbol} 双快照构成变化审计 ({snap_new})
| 指标 | 旧快照 `{snap_old}` | 新快照 `{snap_new}` | 变动 |
|---|---:|---:|---:|
| 吸筹大户数 | {s_old['acc_count']} | {s_new['acc_count']} | +{s_new['acc_count']-s_old['acc_count']} |
| 吸筹均分 | {s_old['avg_acc_score']:.6f} | {s_new['avg_acc_score']:.6f} | +{s_new['avg_acc_score']-s_old['avg_acc_score']:.6f} |
| 系统地址数 | {s_old['system_addr_count']} | {s_new['system_addr_count']} | {s_new['system_addr_count']-s_old['system_addr_count']} |
| 非系统钱包 | {s_old['non_system_count']} | {s_new['non_system_count']} | +{s_new['non_system_count']-s_old['non_system_count']} |
| 保留/进入/退出 | — | — | {len(inter)}/{len(entered)}/{len(exited)} |
| 换手率 (Turnover) | — | — | {turnover:.2%} |
| 换入中吸筹大户 | — | — | {acc_pct_str} |
| 换入中系统地址 | — | — | {sys_pct_str} |
"""
        audit_sections.append(audit_md)

    # F. 拼装报告 (全新 tips 方式：表头为纯中文，不含任何 HTML span 标签)
    do = pd.DataFrame(out)
    if not do.empty:
        do = do.sort_values(by='time', ascending=False)
        h = "| 代币 | 时间 | 大户增量(2期) | 平均分 | 分数变动(2期) | 状态 | 3d收益 | 7d收益 | 触发来源 | 级别 | 池地址 |"
        d = "|---|---|---|---|---|---|---|---|---|---|---|"
        rs = [f"| {r['sym']} | {r['time']} | {r['da']} | {r['score']} | {r['ds']} | {r['status']} | {r['ret']} | {r['ret_7d']} | {r['trig_tag']} | {r['lv']} | {r['pool']} |" for _, r in do.iterrows()]
        table_md = "\n".join([h, d] + rs)
    else:
        table_md = "*当前未检测到符合常规或强异动级别的快照断点*"

    run_id = manifest['run_id']
    asset_cnt = manifest['asset_count']
    
    audit_body = "\n".join(audit_sections)

    report_md = f"""# 🚨 剧烈突发吸筹风控专报

> 物理逆源 ID: `{run_id}` | 隔离库物化: {asset_cnt} 资产 | 价格时钟上限: `{latest_price_time}`
> 本报告采用 2&3 快照级联判定逻辑 (Dual-Gate Cascade)，对突发脉冲异动与中长周期累积吸筹事件做无缝全覆盖。本期共有 {asset_cnt} 个活跃资产参与了异动审计。

---

{audit_body}

---

### 跨币构成变化触发明细 (SNAPSHOT_COMPOSITION_SHIFT)

{table_md}
"""

    os.makedirs(REPORT_DIR, exist_ok=True)
    filename = f"anomaly_intense_surge_{datetime.now().strftime('%m%d_%H%M')}.md"
    filepath = os.path.join(REPORT_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(report_md)
        
    val_conn.close()
    elapsed = round(time.time() - t0, 2)
    print(f"📄 报告成功输出到: {filepath} ({len(report_md)} bytes, 耗时 {elapsed}s)")

if __name__ == '__main__':
    import time
    generate_report()
