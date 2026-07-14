import sqlite3
import os
import pandas as pd
from datetime import datetime, timedelta

def run_audit(sum_db_path="/opt/AI-SUM/select-sum.db"):
    if not os.path.exists(sum_db_path):
        print(f"Database not found at {sum_db_path}")
        return
    
    conn = sqlite3.connect(sum_db_path)
    
    # 1. 提取最近 7 天的历史记录
    # 考虑可能包含新字段 rolling_7d_ret，通过 try-except 进行降级处理
    query = """
        SELECT computed_date, token_symbol, signal_level, entry_price, 
               price_7d_ret, price_now_ret, mdd, 
               volume_24h, reserve_usd, buy_tx_pct, turnover_ratio,
               retention_24h, retention_72h, pnl_ratio,
               macro_score, micro_score, consec_acc, whale_divergence, concentration
        FROM token_history
        WHERE computed_date >= date('now', '-7 days') AND computed_date <= date('now')
    """
    
    try:
        df = pd.read_sql_query(query, conn)
    except Exception:
        # 降级：如果某些字段还未生成
        query_fallback = """
            SELECT computed_date, token_symbol, signal_level, entry_price, 
                   price_7d_ret, price_now_ret, mdd, 
                   volume_24h, reserve_usd, buy_tx_pct, turnover_ratio, consec_acc
            FROM token_history
            WHERE computed_date >= date('now', '-7 days') AND computed_date <= date('now')
        """
        df = pd.read_sql_query(query_fallback, conn)
        # 补全缺失列为 None
        for col in ['retention_24h', 'retention_72h', 'pnl_ratio', 'macro_score', 'micro_score', 'whale_divergence', 'concentration']:
            if col not in df.columns:
                df[col] = None

    # 尝试读取 rolling_7d_ret 字段，如果不存在，则在 DataFrame 里动态计算
    has_rolling = False
    try:
        df_rolling = pd.read_sql_query("SELECT id, rolling_7d_ret FROM token_history LIMIT 1", conn)
        has_rolling = True
    except Exception:
        pass
    
    if has_rolling:
        query_with_rolling = query.replace("price_now_ret,", "price_now_ret, rolling_7d_ret,")
        try:
            df = pd.read_sql_query(query_with_rolling, conn)
        except Exception:
            df['rolling_7d_ret'] = None
    else:
        df['rolling_7d_ret'] = None

    if len(df) == 0:
        print("No records found in last 7 days.")
        conn.close()
        return

    # 对每个代币保留最新一条记录进行去重
    df_latest = df.sort_values('computed_date').groupby('token_symbol').last().reset_index()

    # 如果没有 rolling_7d_ret 且有 price_now_ret，可用 price_now_ret 近 7 天差分来估算
    # 或者如果 price_7d_ret 有效，我们直接看已结算 7d 收益的偏离度
    df_latest['display_7d_ret'] = df_latest['price_7d_ret']
    
    # 偏离度计算: 
    # 看涨信号（DIAMOND/YELLOW）价格下跌越多，偏离度越大
    # 看跌信号（RED）价格上涨越多，偏离度越大
    def calc_deviation(row):
        ret = row['display_7d_ret']
        sig = row['signal_level']
        if pd.isna(ret):
            # 未满 7 天，使用 now_ret 代替做阶段估算
            ret = row['price_now_ret']
        if pd.isna(ret):
            return 0
        if sig in ['DIAMOND', 'YELLOW']:
            return -ret
        elif sig == 'RED':
            return ret
        return 0
        
    df_latest['deviation'] = df_latest.apply(calc_deviation, axis=1)

    # 筛选有信号的代币
    df_sig = df_latest[df_latest['signal_level'].isin(['DIAMOND', 'YELLOW', 'RED'])].copy()

    # 1. 严重看涨误判 (信号 DIAMOND/YELLOW, 但大跌)
    df_bull_bad = df_sig[df_sig['signal_level'].isin(['DIAMOND', 'YELLOW'])].sort_values('deviation', ascending=False).head(10)
    
    # 2. 严重看跌误判 (信号 RED, 但大涨)
    df_bear_bad = df_sig[df_sig['signal_level'] == 'RED'].sort_values('deviation', ascending=False).head(10)

    # 3. 完美命中看涨 (信号 DIAMOND/YELLOW, 大涨)
    df_bull_good = df_sig[df_sig['signal_level'].isin(['DIAMOND', 'YELLOW'])].sort_values('deviation', ascending=True).head(10)

    # 4. 完美命中看跌 (信号 RED, 大跌)
    df_bear_good = df_sig[df_sig['signal_level'] == 'RED'].sort_values('deviation', ascending=True).head(10)

    # 生成 Markdown 报表
    today_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report = []
    report.append(f"# 168小时信号偏离度审计报告 (Deviation Audit)")
    report.append(f"\n> 审计时间: {today_str} | 数据窗口: 最近 168 小时")
    report.append("\n## 🔬 方法论说明")
    report.append("- **偏离度定义**：看涨信号（DIAMOND/YELLOW）后续下跌，或看跌信号（RED）后续上涨。偏离值（绝对值）越大说明模型误差越严重。")
    report.append("- **优化方向**：对严重偏离的个案进行白盒审计（DEX LP、交易量、持仓集中度、连续吸筹天数），为评分阀值提供调整依据。")
    
    headers = "| 代币 | 信号 | 7d/当前收益 | 最大回撤 | 24h交易量 | DEX LP | 换手率 | 吸筹浓度 | 连续吸筹 | 庄浮盈率 |"
    divider = "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
    
    def format_row(r):
        ret_val = r['display_7d_ret'] if not pd.isna(r['display_7d_ret']) else r['price_now_ret']
        ret_str = f"{ret_val:+.2f}%" if not pd.isna(ret_val) else "—"
        mdd_str = f"{r['mdd']:.2f}%" if not pd.isna(r['mdd']) else "—"
        vol_str = f"${r['volume_24h']/1e3:.1f}K" if r['volume_24h'] else "—"
        lp_str = f"${r['reserve_usd']/1e3:.1f}K" if r['reserve_usd'] else "—"
        turnover = f"{r['turnover_ratio']:.2f}%" if not pd.isna(r['turnover_ratio']) else "—"
        conc = f"{r['concentration']:.1f}%" if not pd.isna(r['concentration']) else "—"
        consec = str(int(r['consec_acc'])) if not pd.isna(r['consec_acc']) else "—"
        pnl = f"{r['pnl_ratio']:.1f}%" if not pd.isna(r['pnl_ratio']) else "—"
        sig = r['signal_level']
        if sig == 'DIAMOND':
            sig = "💎 DIAMOND"
        elif sig == 'RED':
            sig = "🔴 RED"
        elif sig == 'YELLOW':
            sig = "🟡 YELLOW"
        return f"| **{r['token_symbol']}** | {sig} | {ret_str} | {mdd_str} | {vol_str} | {lp_str} | {turnover} | {conc} | {consec} | {pnl} |"

    report.append("\n## 🚨 Top 10 严重看涨误判 (DIAMOND/YELLOW 但暴跌/大跌)")
    report.append(headers)
    report.append(divider)
    if len(df_bull_bad) > 0:
        for _, row in df_bull_bad.iterrows():
            report.append(format_row(row))
    else:
        report.append("| — | — | — | — | — | — | — | — | — | — |")

    report.append("\n## 🚨 Top 10 严重看跌误判 (RED 但逆势暴涨)")
    report.append(headers)
    report.append(divider)
    if len(df_bear_bad) > 0:
        for _, row in df_bear_bad.iterrows():
            report.append(format_row(row))
    else:
        report.append("| — | — | — | — | — | — | — | — | — | — |")

    report.append("\n## ✅ Top 10 完美命中看涨 (DIAMOND/YELLOW 成功捕获暴涨)")
    report.append(headers)
    report.append(divider)
    if len(df_bull_good) > 0:
        for _, row in df_bull_good.iterrows():
            report.append(format_row(row))
    else:
        report.append("| — | — | — | — | — | — | — | — | — | — |")

    report.append("\n## 🛡️ Top 10 完美避险看跌 (RED 成功预警暴跌)")
    report.append(headers)
    report.append(divider)
    if len(df_bear_good) > 0:
        for _, row in df_bear_good.iterrows():
            report.append(format_row(row))
    else:
        report.append("| — | — | — | — | — | — | — | — | — | — |")

    report_content = "\n".join(report)
    
    # 写入报告文件
    report_path = "/opt/AI-SUM/report/history/deviation_report.md"
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_content)
    
    print(f"Deviation audit report generated successfully at {report_path}")
    conn.close()

if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else "/opt/AI-SUM/select-sum.db"
    run_audit(db)
