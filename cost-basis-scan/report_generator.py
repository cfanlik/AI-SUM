"""
cost-basis-scan 报告生成器
终端输出 + Markdown 报告
"""
from __future__ import annotations
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from verdict_engine import VerdictResult
import config

# ── 裁决分组定义 ──
TRANSITION_VERDICTS = {"DEATH_SPIRAL", "SQUEEZE_ACC", "SQUEEZE_ACC_LOW", "SQUEEZE_ACC_MED", "SQUEEZE_ACC_HIGH"}
ACC_VERDICTS = {"STEALTH_ACC", "IRON_HOLD", "ACCUMULATING"}
DIST_VERDICTS = {"PROFIT_EXIT", "LIQUIDITY_CRISIS", "BAG_PASSING", "DISTRIBUTING"}


def generate_report(
    results: list[VerdictResult],
    scan_time: str,
    total_tokens: int,
    skipped_g1: int,
    skipped_g2: int,
) -> str:
    """生成终端输出 + MD 报告文件，返回 MD 文件路径"""

    # 分组
    transition_list = [r for r in results if r.verdict in TRANSITION_VERDICTS]
    acc_list = [r for r in results if r.verdict in ACC_VERDICTS]
    dist_list = [r for r in results if r.verdict in DIST_VERDICTS]
    neutral_list = [r for r in results if r.verdict == "NEUTRAL"]

    # 排序（按置信度降序）
    acc_list.sort(key=lambda r: r.acc_pct, reverse=True)
    dist_list.sort(key=lambda r: r.dist_pct, reverse=True)

    # ── 终端输出 ──
    print(f"\n{'='*70}")
    print(f"💰 cost-basis-scan | {scan_time}")
    print(f"   扫描 {total_tokens} 代币 | G1跳过 {skipped_g1} | G2跳过 {skipped_g2}")
    print(f"   有效 {len(results)} | 吸筹:{len(acc_list)} 出货:{len(dist_list)} "
          f"跃迁:{len(transition_list)} 中性:{len(neutral_list)}")
    print(f"{'='*70}")

    if transition_list:
        print(f"\n⚡ 状态跃迁预警 ({len(transition_list)} 个)")
        print(f"{'代币':<8} {'链':<5} {'判定':<20} {'现价':>10} {'VWAP':>10} "
              f"{'暴利区%':>8} {'历史':>10} {'信号':<12}")
        print("-" * 90)
        for r in transition_list:
            print(f"{r.token_symbol:<8} {r.chain:<5} {r.verdict:<20} "
                  f"${r.gecko_price:<9.6f} ${r.vwap:<9.6f} "
                  f"{r.windfall_pct:>7.1f}% {r.watchlist_ref:>10} {r.triggered_signals:<12}")

    if acc_list:
        print(f"\n🎯 吸筹信号 ({len(acc_list)} 个)")
        print(f"{'代币':<8} {'链':<5} {'判定':<16} {'ACC%':>6} {'现价':>10} "
              f"{'VWAP':>10} {'CV':>6} {'重心漂移':>10} {'信号':<12}")
        print("-" * 95)
        for r in acc_list[:15]:
            print(f"{r.token_symbol:<8} {r.chain:<5} {r.verdict:<16} "
                  f"{r.acc_pct:>5.1f}% ${r.gecko_price:<9.6f} ${r.vwap:<9.6f} "
                  f"{r.cost_cv:>5.3f} {r.gravity_drift_label:>10} {r.triggered_signals:<12}")

    if dist_list:
        print(f"\n💀 出货信号 ({len(dist_list)} 个)")
        print(f"{'代币':<8} {'链':<5} {'判定':<20} {'DIST%':>6} {'现价':>10} "
              f"{'VWAP':>10} {'暴利区%':>8} {'LP($)':>12} {'信号':<12}")
        print("-" * 100)
        for r in dist_list[:15]:
            print(f"{r.token_symbol:<8} {r.chain:<5} {r.verdict:<20} "
                  f"{r.dist_pct:>5.1f}% ${r.gecko_price:<9.6f} ${r.vwap:<9.6f} "
                  f"{r.windfall_pct:>7.1f}% ${r.lp_usd:>11,.0f} {r.triggered_signals:<12}")

    # ── Markdown 报告 ──
    md = _build_md(results, scan_time, total_tokens, skipped_g1, skipped_g2,
                   transition_list, acc_list, dist_list)

    # 写入文件
    report_dir = Path(config.REPORT_DIR)
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    report_path = report_dir / f"cb_{ts}.md"
    report_path.write_text(md, encoding="utf-8")
    print(f"\n📄 报告: {report_path}")

    return str(report_path)


def _pnl_label(gecko_price: float, vwap: float) -> str:
    """生成浮盈率标签: '浮盈+34%' 或 '浮亏-25%'"""
    if vwap <= 0 or gecko_price <= 0:
        return "N/A"
    pnl = (gecko_price - vwap) / vwap * 100
    if pnl >= 0:
        return f"+{pnl:.0f}%"
    return f"{pnl:.0f}%"


def _pnl_interpret(gecko_price: float, vwap: float) -> str:
    """生成解读文本"""
    if vwap <= 0 or gecko_price <= 0:
        return ""
    pnl = (gecko_price - vwap) / vwap * 100
    if pnl >= 0:
        return f"盈利加仓(浮盈{pnl:.0f}%仍买入)"
    return f"水下加仓(被套{abs(pnl):.0f}%仍买入)"


def _build_md(
    results, scan_time, total_tokens, skipped_g1, skipped_g2,
    transition_list, acc_list, dist_list,
) -> str:
    lines = [
        f"# 💰 成本基础扫描 (cost-basis-scan) — {scan_time}",
        "",
        f"> 扫描 **{total_tokens}** 代币 | G1跳过 {skipped_g1} | G2跳过 {skipped_g2} "
        f"| 有效 **{len(results)}**",
        ">",
        "> **数据来源**: `select.db` (持仓/成本/市场) + `select-sum.db` (历史对比)",
        "> **运行模式**: 全部离线分析，无运行时外部请求",
        "",
        "---",
        "",
    ]



    # ── 状态跃迁 ──
    if transition_list:
        lines.append(f"## ⚡ 状态跃迁预警 — {len(transition_list)} 个")
        lines.append("")
        lines.append("> **触发机制**: 该代币历史上被 master-scan 标记为 RED/DIAMOND（筹码异动），")
        lines.append("> 且当前扫描发现大户在成本高于现价时继续加仓(C1) 或 暴利区开始出逃(C4)")
        lines.append(">")
        lines.append("> **SQUEEZE_ACC(逼空吸筹)**: 历史RED + C1水下加仓 → 庄家被套不跑反而加仓，后续大概率拉升")
        lines.append("> - _LOW: 仅C1单信号 | _MED: C1+C2(一致行动人)或有历史对比 | _HIGH: C1+C2+有历史对比")
        lines.append("> **DEATH_SPIRAL(终极雪崩)**: 历史DIAMOND + C4暴利出逃 → 吸筹完成后的派发末期，随时崩盘")
        lines.append(">")
        lines.append("> **对比数据**: `select-sum.db → watchlist` 历史状态 × 当前成本信号")
        lines.append("")
        lines.append("| 代币 | 链 | 判定 | 现价 | VWAP | 浮盈率 | 暴利区% | 历史状态 | 解读 | 信号 |")
        lines.append("|------|----|----|------|------|--------|---------|---------|------|------|")
        for r in transition_list:
            pnl = _pnl_label(r.gecko_price, r.vwap)
            interpret = _pnl_interpret(r.gecko_price, r.vwap)
            lines.append(
                f"| {r.token_symbol} | {r.chain} | {r.verdict} "
                f"| ${r.gecko_price:.6f} | ${r.vwap:.6f} "
                f"| {pnl} | {r.windfall_pct:.1f}% "
                f"| {r.watchlist_ref} | {interpret} | {r.triggered_signals} |"
            )
        lines.append("")

    # ── 吸筹 ──
    if acc_list:
        lines.append(f"## 🎯 吸筹信号 — {len(acc_list)} 个")
        lines.append("")
        lines.append("> **STEALTH_ACC(水下吸筹)**: C1(水下加仓) + C2(成本聚集) → 多个大户在相同价位被套但继续买入，一致行动人特征")
        lines.append("> **IRON_HOLD(铁底锁仓)**: C2(成本聚集) + C3(暴利不卖) → 大户成本极低且零卖出，坚定锁仓等待拉升")
        lines.append("> **ACCUMULATING(吸筹)**: ACC置信度 ≥ 50%，存在吸筹行为但未达到组合裁决条件")
        lines.append("")
        lines.append("| 代币 | 链 | 判定 | ACC% | 现价 | VWAP | 浮盈率 | 成本CV | 重心漂移 | 信号 |")
        lines.append("|------|----|----|------|------|------|--------|--------|---------|------|")
        for r in acc_list:
            pnl = _pnl_label(r.gecko_price, r.vwap)
            lines.append(
                f"| {r.token_symbol} | {r.chain} | {r.verdict} "
                f"| {r.acc_pct:.1f}% | ${r.gecko_price:.6f} | ${r.vwap:.6f} "
                f"| {pnl} | {r.cost_cv:.3f} | {r.gravity_drift_label} | {r.triggered_signals} |"
            )
        lines.append("")

    # ── 出货 ──
    if dist_list:
        lines.append(f"## 💀 出货信号 — {len(dist_list)} 个")
        lines.append("")
        lines.append("> **LIQUIDITY_CRISIS(流动性危机)**: C4(暴利出逃) + C6(LP不足) → 暴利区占比>40%且池子LP<$500K，一旦砸盘归零")
        lines.append("> **PROFIT_EXIT(浮盈出逃)**: C4(暴利出逃) + DIST≥50% → 底仓大户开始获利了结")
        lines.append("> **BAG_PASSING(筹码转移)**: C5(高位派发) + C7(成本倒挂) → 庄家将筹码派发给高成本散户接盘")
        lines.append("> **DISTRIBUTING(出货)**: DIST置信度 ≥ 50%，存在出货行为但未达到组合裁决条件")
        lines.append("")
        lines.append("| 代币 | 链 | 判定 | DIST% | 现价 | VWAP | 浮盈率 | 暴利区% | LP($) | 信号 |")
        lines.append("|------|----|----|-------|------|------|--------|---------|-------|------|")
        for r in dist_list:
            pnl = _pnl_label(r.gecko_price, r.vwap)
            lines.append(
                f"| {r.token_symbol} | {r.chain} | {r.verdict} "
                f"| {r.dist_pct:.1f}% | ${r.gecko_price:.6f} | ${r.vwap:.6f} "
                f"| {pnl} | {r.windfall_pct:.1f}% | ${r.lp_usd:,.0f} | {r.triggered_signals} |"
            )
        lines.append("")

    # ── 成本带分布统计 ──
    signaled = [r for r in results if r.verdict != "NEUTRAL"]
    if signaled:
        lines.append("## 📊 成本带分布（有信号代币）")
        lines.append("")
        lines.append("> **成本带含义**: 深水区(成本>现价×1.3, 严重套牢) | 浅水区(成本≈现价, 轻微浮亏)")
        lines.append("> | 盈利区(成本<现价, 安全垫) | 暴利区(成本<现价×0.5, 堰塞湖随时可砸)")
        lines.append(">")
        lines.append("> **重心漂移**: LOCK_PUMP=锁仓拉升 | CHURN_OUT=换手出货 | DIAMOND_HAND=钻石手 | STABLE=稳定 | FIRST_SCAN=首次扫描")
        lines.append("")
        lines.append("| 代币 | 深水区% | 浅水区% | 盈利区% | 暴利区% | 成本重心 | 重心漂移 |")
        lines.append("|------|---------|---------|---------|---------|---------|---------|")
        for r in signaled:
            lines.append(
                f"| {r.token_symbol} | {r.deep_underwater_pct:.1f}% "
                f"| {r.shallow_underwater_pct:.1f}% | {r.profit_zone_pct:.1f}% "
                f"| {r.windfall_pct:.1f}% | ${r.cost_gravity:.6f} "
                f"| {r.gravity_drift_label} |"
            )
        lines.append("")

    # ── 锁仓 Top 10 独立看板 (平行新增章节，独立于吸筹/出货/跃迁等信号大类，绝不进行任何替换或覆盖) ──
    lock_top10 = _fetch_global_lock_top10()
    if lock_top10:
        lines.append("## 📊 本期活跃代币强锁仓 Top 10 (只买不卖/极低卖出汇总)")
        lines.append("")
        lines.append("> **统计口径**: 仅计算非 CEX/DEX/Contract/Supernode 且 `buy_amt_usd > 0` 的大户地址 (大户样本数 >= 3)，按只买不卖人数占比降序")
        lines.append("")
        lines.append("| 排名 | 代币 | 链 | 总大户数 | 只买不卖人数 (占比) | 只买不卖持仓% | 卖出<1%人数 (占比) | 卖出<1%持仓% | 卖出<3%人数 (占比) | 卖出<3%持仓% |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for idx, r in enumerate(lock_top10):
            rank = idx + 1
            ob_pct = r["ob_pct"]
            s1_pct = r["s1_pct"]
            s3_pct = r["s3_pct"]
            
            ob_str = f"{r['ob_cnt']} ({ob_pct:.1f}%)"
            s1_str = f"{r['s1_cnt']} ({s1_pct:.1f}%)"
            s3_str = f"{r['s3_cnt']} ({s3_pct:.1f}%)"
            
            lines.append(
                f"| **No.{rank}** | {r['symbol']} | `{r['chain']}` | {r['total_acc']} | {ob_str} | {r['ob_hp']:.2f}% | {s1_str} | {r['s1_hp']:.2f}% | {s3_str} | {r['s3_hp']:.2f}% |"
            )
        lines.append("")

    # ── 对比机制说明 ──
    lines.append("---")
    lines.append("")
    lines.append("## 📋 数据来源与对比机制")
    lines.append("")
    lines.append("| 对比维度 | 数据表 | 数据库 | 作用 |")
    lines.append("|----------|--------|--------|------|")
    lines.append("| 当前成本画像 | `bubblemap_holders` | select.db | gmgn_avg_price/buy_cnt/sell_cnt → 四成本带分层 |")
    lines.append("| 当前市场价格 | `gecko_market_data` | select.db | price_usd/reserve_usd → 浮盈率计算基准 |")
    lines.append("| 历史筹码异动 | `watchlist` | select-sum.db | master-scan 的 RED/DIAMOND 标记 → 状态跃迁判定 |")
    lines.append("| 成本重心漂移 | `cost_basis_snapshots` | select-sum.db | 上次扫描 VWAP/重心 → 本次对比判定漂移方向 |")
    lines.append("")
    lines.append("## 📖 信号编码速查")
    lines.append("")
    lines.append("| 编码 | 维度 | 含义 |")
    lines.append("|------|------|------|")
    lines.append("| C1 | ACC | 水下逆势加仓: 深水/浅水区大户 buy_cnt 增加且持仓未减 |")
    lines.append("| C2 | ACC | 成本聚集: 大户买入均价 CV<0.15，一致行动人特征 |")
    lines.append("| C3 | ACC | 暴利区坚定持有: 暴利区地址中 sell_cnt=0 占比≥50% |")
    lines.append("| C4 | DIST | 暴利出逃: 暴利区出现卖出且持仓减少的地址 |")
    lines.append("| C5 | DIST | 高位派发: 新增地址成本≈现价 + 老鲸鱼持仓下降 |")
    lines.append("| C6 | STRUCT | 流动性危机: 暴利区持仓>40% 且 LP<$500K |")
    lines.append("| C7 | STRUCT | 成本倒挂: Top5大户VWAP > 尾部地址VWAP (大户被套) |")
    lines.append("| C8 | DIST | 暴利区48h净流出: 暴利区地址 48h 净卖出 > 持仓 10% |")
    lines.append("")
    lines.append("---")
    lines.append(f"*生成时间: {datetime.now().isoformat()}*")

    return "\n".join(lines)


def _fetch_global_lock_top10() -> list[dict]:
    import sqlite3
    db_path = "/opt/select-coin/data/select.db"
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 1. Fetch active tokens
        cursor.execute("SELECT DISTINCT chain, token_address FROM token_scores;")
        candidates = cursor.fetchall()
        
        # 2. Fetch symbol mapping
        cursor.execute("SELECT chain, token_address, symbol FROM token_names;")
        token_symbols = {(r[0], r[1]): r[2] for r in cursor.fetchall()}
        
        stats = []
        for c in candidates:
            chain, token_addr = c[0], c[1]
            
            cursor.execute("SELECT MAX(snapshot_time) FROM bubblemap_holders WHERE chain = ? AND token_address = ?;", (chain, token_addr))
            res = cursor.fetchone()
            if not res or res[0] is None:
                continue
            max_time = res[0]
            
            cursor.execute("""
                SELECT hold_percentage, buy_amt_usd, sell_amt_usd 
                FROM bubblemap_holders 
                WHERE chain = ? AND token_address = ? AND snapshot_time = ?
                  AND is_cex = 0 AND is_dex = 0 AND is_contract = 0 AND is_supernode = 0 AND buy_amt_usd > 0;
            """, (chain, token_addr, max_time))
            holders = cursor.fetchall()
            total_acc = len(holders)
            if total_acc < 3:
                continue
                
            ob_cnt = 0
            ob_hp = 0.0
            s1_cnt = 0
            s1_hp = 0.0
            s3_cnt = 0
            s3_hp = 0.0
            for h in holders:
                hold_pct = h[0] if h[0] is not None else 0.0
                buy_amt = h[1]
                sell_amt = h[2] if h[2] is not None else 0.0
                
                if sell_amt == 0.0:
                    ob_cnt += 1
                    ob_hp += hold_pct
                if sell_amt < buy_amt * 0.01:
                    s1_cnt += 1
                    s1_hp += hold_pct
                if sell_amt < buy_amt * 0.03:
                    s3_cnt += 1
                    s3_hp += hold_pct
                    
            ob_pct = (ob_cnt / total_acc) * 100
            s1_pct = (s1_cnt / total_acc) * 100
            s3_pct = (s3_cnt / total_acc) * 100
            symbol = token_symbols.get((chain, token_addr), "UNKNOWN")
            
            stats.append({
                "chain": chain,
                "token_address": token_addr,
                "symbol": symbol,
                "total_acc": total_acc,
                "ob_cnt": ob_cnt,
                "ob_pct": ob_pct,
                "ob_hp": ob_hp,
                "s1_cnt": s1_cnt,
                "s1_pct": s1_pct,
                "s1_hp": s1_hp,
                "s3_cnt": s3_cnt,
                "s3_pct": s3_pct,
                "s3_hp": s3_hp
            })
        conn.close()
        stats.sort(key=lambda x: (x["ob_pct"], x["total_acc"]), reverse=True)
        return stats[:10]
    except Exception as e:
        return []
