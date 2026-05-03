# AI-SUM 六引擎综合分析系统

> 版本: V2.0 | 更新: 2026-05-03 | VPS: `/opt/AI-SUM/`
> GitHub: https://github.com/cfanlik/AI-SUM | DB: `select-sum.db`

---

## 系统架构

```
select-coin (数据源)              AI-SUM (分析层)
───────────────────              ──────────────────
bubblemap_holders ──┐            ┌─ master-scan  → watchlist
gecko_market_data ──┼── src.db ──┤  opus-scan    → opus_snapshots
                    │            ├─ unified-scan → unified_results
                    │            ├─ whale-scan   → whale_snapshots
                    │            ├─ cost-basis   → cost_basis_snapshots
                    │            └─ meta-verdict → meta_snapshots + token_lifecycle
                    │                                     ↓
                    │                              report/meta/*.md
```

### 目录结构

```
/opt/AI-SUM/
├── master-scan/         # 引擎1: Top300 持有者差分分析
├── opus-scan/           # 引擎2: 双维度 ACC/DIST 判定
├── unified-scan/        # 引擎3: master+opus 统一交叉信号
├── bigcoin/             # 引擎4: whale 庄控检测
├── cost-basis-scan/     # 引擎5: 成本基础 VWAP 分层
├── meta-verdict/        # ★ 仲裁层: 五引擎汇总
│   ├── run.py           # 主入口
│   ├── config.py        # 积分权重
│   ├── collector.py     # 五表数据收集 + 价格补全
│   ├── arbitrator.py    # 加权仲裁 + 出货抑制 + 生命周期
│   ├── trend_analyzer.py# 跨轮趋势分析
│   ├── conflict_detector.py  # 引擎矛盾检测(5规则)
│   └── report_generator.py   # 终端+MD 双输出(7区块)
├── persist_helper.py    # 引擎持久化注入（opus/whale/unified）
├── select-sum.db        # 分析数据库
├── report/              # 报告输出目录
│   ├── v5/              # master-scan 报告
│   ├── opus/            # opus-scan 报告
│   ├── unified/         # unified-scan 报告
│   ├── whale/           # whale-scan 报告
│   ├── cost-basis/      # cost-basis-scan 报告
│   └── meta/            # meta-verdict 综合报告
└── log/                 # 运行日志
```

---

## 定时任务 (Crontab)

```crontab
0  0,12 * * * cd /opt/AI-SUM && python3 master-scan/run.py    >> log/master.log 2>&1
10 0,12 * * * cd /opt/AI-SUM && python3 opus-scan/run.py --offline    >> log/opus.log 2>&1
20 0,12 * * * cd /opt/AI-SUM && python3 unified-scan/run.py --offline >> log/unified.log 2>&1
30 0,12 * * * cd /opt/AI-SUM && python3 bigcoin/run.py        >> log/whale.log 2>&1
40 0,12 * * * cd /opt/AI-SUM && python3 cost-basis-scan/run.py >> log/cost-basis.log 2>&1
50 0,12 * * * cd /opt/AI-SUM && python3 meta-verdict/run.py   >> log/meta.log 2>&1
```

**执行顺序**: master(00) → opus(10) → unified(20) → whale(30) → CB(40) → meta(50)
**频率**: 每天 2 轮（00:xx / 12:xx UTC）
**依赖链**: opus/unified/CB 依赖 master 的 watchlist → 间隔 10 分钟足够（master 通常 3-5 分钟完成）

---

## 引擎详情

### 1. master-scan — Top300 持有者差分分析

- 读取 `src.bubblemap_holders` 多快照横向差分
- 信号: DIAMOND（极低流通+真金吸筹）/ RED（异常行为）/ YELLOW（观察）
- 输出: `watchlist` 表 + `report/v5/radar_*.md`
- 关键参数: 双90%阈值（机构控盘≥90% + DEX真金≥90%触发DIAMOND）

### 2. opus-scan — 双维度吸筹/出货判定

- 读取 `src.bubblemap_holders` 最新快照
- 双维度: `acc_confidence`（吸筹置信度）+ `dist_confidence`（出货置信度）
- 输出: `opus_snapshots` 表 + `report/opus/opus_*.md`
- 判定: `acc ≥ 50% → ACCUMULATING` / `dist ≥ 50% → SLOW_DISTRIBUTION`

### 3. unified-scan — 交叉信号引擎

- 合并 master + opus + 结构数据的多维交叉
- 信号: DIAMOND / STRONG_ACC / MODERATE_ACC / SLOW_DISTRIBUTION / WHALE_DUMP
- 触发信号: A1(master DIAMOND) / A2(master RED) / D1(CEX流入) / D2(出货者) / S1(极端集中) / S2(M/L比)
- 输出: `unified_results` 表 + `report/unified/unified_*.md`

### 4. whale-scan (bigcoin) — 庄控检测

- 分析 Top2%/Top10% 持仓集中度 + 漂移率
- 信号: HIGH(≥65%) / MEDIUM(≥50%) / LOW(≥35%) / CLEAN
- 输出: `whale_snapshots` 表 + `report/whale/whale_*.md`

### 5. cost-basis-scan — 成本基础分层

- 读取 `src.bubblemap_holders`(gmgn_avg_price) + `src.gecko_market_data`(price_usd)
- 四成本带: 深水区(>1.3x) / 浅水区(≈1x) / 盈利区(<1x) / 暴利区(<0.5x)
- 8 信号(C1-C8): C1水下加仓 / C2一致行动人 / C4暴利出逃 / C8暴利净流出
- 状态跃迁: `watchlist RED/DIAMOND × C1 → SQUEEZE_ACC` / `DIAMOND × C4 → DEATH_SPIRAL`
- SQUEEZE_ACC 三级: LOW(仅C1) / MED(C1+C2) / HIGH(C1+C2+历史对比)
- 快照间距>48h → C1 权重降权
- 输出: `cost_basis_snapshots` 表 + `report/cost-basis/cb_*.md`

---

## meta-verdict — 五引擎综合仲裁层

### 积分权重体系

| 引擎 | 信号 | 积分 |
|------|------|------|
| master | DIAMOND / RED / YELLOW | +4 / +2 / +1 |
| opus | acc_confidence × 0.04 — dist_confidence × 0.04 | 动态 |
| unified | DIAMOND / RED / YELLOW | +4 / +2 / +1 |
| unified(出货) | SLOW_DIST / WHALE_DUMP | -2 / -3 |
| whale | HIGH / MEDIUM / LOW | +3 / +2 / +1 |
| CB | SQUEEZE_ACC_HIGH/STEALTH_ACC | +3 |
| CB | SQUEEZE_ACC_MED | +2 |
| CB | SQUEEZE_ACC_LOW / IRON_HOLD | +1 |
| CB | DEATH_SPIRAL | -5 |
| CB | LIQUIDITY_CRISIS | -3 |
| CB | PROFIT_EXIT | -2 |

### 关键仲裁规则

```
1. DIAMOND 去重: master=DIAMOND 且 unified=DIAMOND → unified 积分 ×0.5
2. 出货抑制: opus/unified/CB 有出货信号时 → master 正分归零
3. EXPIRED 降级: watchlist status=EXPIRED → DIAMOND/RED 降为 YELLOW(+1)
```

### 裁决阈值

```
综合积分 ≥ 3  → 🎯 ACC（吸筹）
综合积分 ≤ -2 → 💀 DIST（出货）
其余         → NEUTRAL
```

### 矛盾检测 (conflict_detector.py)

| 规则 | 条件 | 含义 |
|------|------|------|
| C1 | master=DIAMOND 但 whale 无数据 | 庄控未验证 |
| C2 | unified=SLOW_DIST 但 meta=ACC | 引擎对立 |
| C3 | CB=SQUEEZE_ACC 但 opus 无数据 | 单引擎支撑 |
| C4 | opus=出货 但 meta=ACC | 引擎对立 |
| C5 | 仅 1 引擎命中但 score≥3 | 信号单薄 |

### 趋势分析 (trend_analyzer.py)

- 对比上一轮 meta_snapshots → 新进/退出/积分升降/跃变检测
- 跃变阈值: |delta| ≥ 1.5 → 报告标注原因

### 生命周期状态机

| 阶段 | 触发条件 |
|------|----------|
| 🔒 CONTROLLED | master DIAMOND + whale HIGH |
| 🎯 ACCUMULATING | meta=ACC + master 非 DIAMOND |
| 👀 WATCHLIST | master RED/YELLOW + 综合未达阈值 |
| 💀 DISTRIBUTING | meta=DIST 或 DEATH_SPIRAL/LIQUIDITY_CRISIS |
| ─ NEUTRAL | 无信号 |

跃迁告警: `ACCUMULATING → DISTRIBUTING` 时记录 `⚡ 跃迁` 写入 `token_lifecycle` 表。

### 报告结构（7 区块）

```
1. 📊 概览摘要
2. 🏥 引擎健康状态（5 引擎最后更新时间）
3. 📈 趋势摘要（新进/退出/升降/跃变 + Δ列）
4. 📋 信号变更日志
5. 🎯 吸筹排行（含 Δ + 阶段 + 5 引擎信号 + 价格）
6. 💀 出货预警
7. ⚠ 引擎矛盾（C1-C5 规则）
8. 📋 积分明细（分项积分审计）
```

### 持久化

- `meta_snapshots`: 全部有信号代币（含 NEUTRAL），含 5 列分项积分
- `token_lifecycle`: 生命周期状态 + 跃迁记录

---

## select-sum.db 表结构

| 表 | 写入方 | 关键字段 | 用途 |
|---|--------|---------|------|
| `watchlist` | master-scan | signal_level, status(ACTIVE/EXPIRED) | DIAMOND/RED/YELLOW 历史 |
| `opus_snapshots` | persist_helper | acc_confidence, dist_confidence, verdict | 吸筹/出货置信度 |
| `whale_snapshots` | persist_helper | confidence, level | 庄控检测 |
| `unified_results` | unified-scan | verdict, acc_score, dist_score | 交叉信号 |
| `cost_basis_snapshots` | cost-basis-scan | verdict, vwap, gecko_price, acc_pct | 成本分层 |
| `meta_snapshots` | meta-verdict | meta_score, meta_verdict, stage, 5×分项积分 | 仲裁结果 |
| `token_lifecycle` | meta-verdict | current_stage, prev_stage, transition | 生命周期 |

---

## 运行命令

```bash
# 单引擎
cd /opt/AI-SUM && python3 master-scan/run.py
cd /opt/AI-SUM && python3 opus-scan/run.py --offline
cd /opt/AI-SUM && python3 unified-scan/run.py --offline
cd /opt/AI-SUM && python3 bigcoin/run.py
cd /opt/AI-SUM && python3 cost-basis-scan/run.py
cd /opt/AI-SUM && python3 meta-verdict/run.py

# 单币诊断
cd /opt/AI-SUM && python3 meta-verdict/run.py --symbol GWEI
cd /opt/AI-SUM && python3 cost-basis-scan/run.py --symbol SKYAI
```

---

## 版本历史

| 版本 | 日期 | 关键变更 |
|------|------|----------|
| **meta-verdict V2.0** | **2026-05-03** | EXPIRED纳入+出货抑制+分项积分持久化+矛盾检测(5规则)+全量保存+趋势分析 |
| **meta-verdict V1.5** | **2026-05-03** | P1: 趋势分析+引擎健康自检+报告重构(7区块)+unified出货映射 |
| **meta-verdict V1.0** | 2026-04-29 | 五引擎仲裁层上线; token_lifecycle; 122代币→26吸筹 |
| **cost-basis V1.1** | 2026-04-29 | SQUEEZE_ACC 三级分层; C8暴利区净流出; C1降权 |
| **persist_helper** | 2026-04-29 | opus/whale/unified 引擎持久化注入 |
| **Crontab** | 2026-04-29 | 6引擎 00:xx/12:xx 双轮, 间隔10分钟 |
| cost-basis V1.0 | 2026-04-28 | 8信号成本基础扫描上线 |
| V8.5 | 2026-04-17 | LP/FDV-LP/V-L 三指标 |
| V8.2 | 2026-04-08 | 钻石绞杀区(E模式) |

---

## 实测数据（2026-05-03 20:47）

| 指标 | 数值 |
|------|------|
| 有信号代币 | 158 |
| 🎯 吸筹 | 44 |
| 💀 出货 | 2 (BAS, UB) |
| ⚠ 矛盾 | 7 (C1×4, C3×3) |
| meta_snapshots 保存 | 158 条/轮（含 NEUTRAL） |
| 引擎健康 | master ⚠(8h) opus/whale/CB/unified ✅ |
