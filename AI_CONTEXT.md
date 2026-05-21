# AI-SUM 六引擎综合分析系统

> 版本: V5.0 | 更新: 2026-05-18 | VPS: `/opt/AI-SUM/`
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
│   ├── report_generator.py   # 终端+MD 双输出(7区块)
│   └── history_report.py     # 长期分析报告（回测+迁移+时序+画像）
├── persist_helper.py    # 引擎持久化注入（opus/whale/unified）
├── select-sum.db        # 分析数据库
├── report/              # 报告输出目录
│   ├── v5/              # master-scan 报告
│   ├── opus/            # opus-scan 报告
│   ├── unified/         # unified-scan 报告
│   ├── whale/           # whale-scan 报告
│   ├── cost-basis/      # cost-basis-scan 报告
│   ├── meta/            # meta-verdict 综合报告
│   └── history/         # history_report 长期分析报告
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
55 0,6,12,18 * * * cd /opt/AI-SUM && python3 meta-verdict/history_report.py >> log/history.log 2>&1
```

**执行顺序**: master(00) → opus(10) → unified(20) → whale(30) → CB(40) → meta(50)
**频率**: 引擎每天 2 轮（00:xx / 12:xx UTC），history_report 每6小时（00/06/12/18）
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
- 9 信号(C1-C9): C1水下加仓 / C2一致行动人 / C4暴利出逃 / C8暴利净流出 / C9深度套牢协同持仓
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
| CB | COORDINATED_HOLD | +2 |
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

### history_report.py — 长期分析报告 V5.0

10 模块融合报告，输出 `report/history/history_MMDD_HHMM.md` (每6小时)：

| 模块 | 功能 | 数据源字段 |
|------|------|-----------|
| 信号回测 | 首次信号时间 × 1d/3d/7d/14d/至今收益 + MDD + Precision | price_usd |
| Holder 迁移 | 7d+14d 留存率、遗漏检测(Top10Δ复合条件)、底部吸筹。**V5 新增: 24h/72h 多尺度留存率与 Top10 浓度漂移** | bubblemap_holders |
| **大户行为追踪** | **首次减仓AMBER预警 + DORMANT锁仓统计 + 成本信念度(套牢不割型)** | **bubblemap_holders × cost_basis_snapshots** |
| 积分时序 | 斜率/σ/连续ACC/引擎稳定性，σ<0.05平序列强制归零 | meta_snapshots |
| 信号×收益 | 按综合分/引擎数分桶胜率，样本<5标注⚠ | meta_snapshots |
| **流动性健康度** | 24h量/7d均量/量变化/LP深度/换手率/买卖比/量价背离/枯竭告警 | **volume_24h, reserve_usd, buy_tx_pct, buyers/sellers** |
| **信号质量评估** | 引擎组合矩阵/单引擎Precision/P-R-F1/失败案例/漏网之鱼 | meta_snapshots 5列分项积分 |
| 单币画像 | Top 10 ACC 完整档案，含流动性+MDD+vs昨天Δ | 全字段 |

#### V5 关键增强 (2026-05-18)

| 增强项 | 说明 |
|--------|------|
| **多时间尺度留存** | **引入 24h (1d) 和 72h (3d) 时序留存与 top10 浓度漂移，对短期主力动向更灵敏** |
| **庄家浮盈率** | **基于 (gecko_price - vwap)/vwap 计算庄家浮盈率 (pnl_ratio)，高浮盈强锁仓为极强信心标志** |
| **出货背离检测** | **price_chg > 5% 且 top10_delta_24h < -1% 时，触发“出货背离” (whale_divergence) 预警，在综合看板中通过「庄出逃」列的 🚨 展现** |
| **量价背离扩展** | **vpd 扩展为 4 种类型：价平量增(潜伏吸筹，看板显示 `🔍潜伏`)、价涨量增(突破放量，显示 `🚀突破`)、价涨量缩(显示 `⚠量缩`)、价跌量增(显示 `⚠量增`)，从根源上消除了原 generic `⚠背离` 命名冲突** |

#### V3 关键增强

| 增强项 | 说明 |
|--------|------|
| gecko 字段激活 | 20字段中从2个→10个（volume_24h/6h/1h, buys/sells, reserve_usd, buy_tx_pct, market_cap等） |
| 中位数 | 回测汇总增加 median 列，消除极端值(如SPACE +243%)拉偏 |
| MDD | 信号后最大回撤，贯穿回测+画像+token_history |
| meta补漏 | save_token_history 增加 meta_snapshots ACC/DIST 全量遍历，解决 KAVA 类丢失 |
| 量价背离 | 价涨量缩=拉盘无力 / 价跌量增=恐慌抛售 |
| 流动性枯竭 | volume < 7d_avg * 30% 或 reserve < $10K 触发告警 |
| 引擎组合矩阵 | 统计 M+O/M+W/M+O+W 等组合 of 独立胜率 |
| P/R/F1 | Precision=ACC中盈利比 / Recall=盈利中被ACC命中比 / F1 |
| 失败案例 | ACC but ret < -15% 的代币清单+引擎组合+MDD |
| 漏网之鱼 | 非ACC but ret > +30% 的漏网代币清单 |

#### V3 实测知识（2026-05-05）

| 发现 | 数据 | 含义 |
|------|------|------|
| whale引擎拖低组合胜率 | M+W(32%) vs M+O(52%) | whale_score 权重可能需调降 |
| unified引擎最准 | 56% precision | 但命中数仅9, 样本偏小 |
| 信号F1偏低 | P=55%, R=40%, F1=46% | Recall低=漏网多, 需扩大覆盖 |
| 流动性枯竭≠信号失败 | BSB: ACC+MDD-47%+量衰-80% | holder稳但无人交易=信息茧房 |
| 极端漏网 | TAC(+374%), SPACE(+243%) 非ACC | 单引擎覆盖不足 |

写入 `token_history` 表持久化（V3新增: volume_24h, reserve_usd, buy_tx_pct, turnover_ratio, mdd）。
meta_snapshots ACC/DIST 代币全量写入，解决丢失问题。

---

## select-sum.db 表结构

| 表 | 写入方 | 关键字段 | 用途 |
|---|--------|---------|------|
| `watchlist` | master-scan | signal_level, status(ACTIVE/EXPIRED) | DIAMOND/RED/YELLOW 历史 |
| `opus_snapshots` | persist_helper | acc_confidence, dist_confidence, verdict | 吸筹/出货置信度 |
| `whale_snapshots` | persist_helper | confidence, level | 庄控检测 |
| `unified_results` | unified-scan | verdict, acc_score, dist_score | 交叉信号 |
| `cost_basis_snapshots` | cost-basis-scan | verdict, vwap, gecko_price, acc_pct, cost_cv | 成本分层(含C9) |
| `meta_snapshots` | meta-verdict | meta_score, meta_verdict, stage, 5×分项积分 | 仲裁结果 |
| `token_lifecycle` | meta-verdict | current_stage, prev_stage, transition | 生命周期 |
| `token_history` | history_report | computed_date, signal_first_seen, price_*_ret, retention_*, score_slope, **volume_24h, reserve_usd, buy_tx_pct, turnover_ratio, mdd**, **retention_24h, retention_72h, top10_delta_24h, top10_delta_72h, pnl_ratio, bs_ratio_24h, whale_divergence**, **concentration, macro_score, micro_score** | 长期跟踪(V6双轨与浓度升级) |
| `hop2_tracking` | meta-verdict | scan_time, token_symbol, hop2_acc_pct, hop2_acc_count, hop2_high_count, tier_98/90/30/80_count, entity_count, price_usd | hop2 庄控跟踪(v5) |

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

# 长期 analysis 报告
cd /opt/AI-SUM && python3 meta-verdict/history_report.py
```

---

## 版本历史

| 版本 | 日期 | 关键变更 |
|------|------|----------|
| **故障修复与安全门控** | **2026-05-20** | **回测落库修复与交易量安全门控**：修复 `save_token_history` 参数绑定遗漏 Bug，恢复日常回测数据落库；在 `pump_detector` 的 D1 评分中引入 **Volume Guard（绝对交易量安全门控）**（24h量<$1,000时强制给0分），杜绝死币零交易量缩误判。 |
| **双轨与浓度升级** | **2026-05-19** | **真实用户吸筹浓度与大盘/精英双轨评分**：重新定义吸筹占比为排除交易所及多重合约后的“真实吸筹浓度”并精细化6档打分（D3）；D6均分演进为“精英均分+大盘门控”双轨设计；`history_report` 看板升级为浓度和大盘/精英双轨指标展示，`token_history` 增加 `concentration`, `macro_score`, `micro_score` 落库。 |
| **history_report V5.0** | **2026-05-18** | **多时间尺度吸筹与量能指标追踪**：24h/72h 留存率与漂移、庄家浮盈率、出货背离 (whale_divergence)、vpd 扩展为 4 种类型、`token_history` 增加 7 个字段落库、综合看板扩展 24h留存/浮盈率/庄出逃/评级精细化 |
| **history_report V4.1** | **2026-05-12** | 新增模块2.5「大户行为追踪」: 地址级首次减仓AMBER预警(DORMANT→减仓检测) + 锁仓休眠率统计 + cost_basis交叉信念度评级(💎套牢不割/🔒强锁仓); 200代币批量化 |
| **bugfix** | **2026-05-12** | 修复 `history_report.py` 合约分析模块未定义报错导致报告生成失败的问题 (将 `futures_analysis` 定义移到执行块之前)，补发 11 日和 12 日长期分析报告。 |
| **cost-basis V1.2 + meta V2.1** | **2026-05-11** | C9深度套牢协同持仓信号(weight=6); COORDINATED_HOLD verdict(+2分); 条件: VWAP/价>2.5x+水下>90%+CV<0.20+holders≥30; 全量198代币仅命中SQD 1个(精准无误判); SQD meta_score 6.0→9.0 |
| **history_report V3.0** | **2026-05-05** | 9模块融合: +流动性健康度+信号质量评估+失败案例+漏网之鱼; gecko 20字段激活(2→10); 中位数+MDD+P/R/F1+引擎组合矩阵; meta补漏(ACC 100%覆盖); token_history +5列 |
| **history_report V2.0** | **2026-05-03** | 回测时间基准修复+遗漏检测Top10Δ复合条件+斜率平序列归零+14d校验+单币画像增强+vs昨天Δ+token_history扩展 |
| **history_report V1.0** | **2026-05-03** | 4模块融合: 信号回测+holder迁移+积分时序+信号×收益 |
| **hop2 跟踪 V5.1** | **2026-05-09** | history_report.py hop2 Top10 增加排名趋势列(↑↓/🆕)+浓度色标(🟡🟣); select-coin API 保留; 独立Tab移除 |
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

## 实测数据（2026-05-05 21:26）

| 指标 | 数值 |
|------|------|
| 有信号代币 | 153 |
| 🎯 吸筹 | 41 |
| 💀 出货 | 2 |
| token_history 覆盖 | ACC 41/41 (100%, V3修复) |
| history_report 模块 | 10 (V4.1: +1大户行为) |
| gecko 字段使用 | 10/20 (V3: 从2→10) |
| 信号 Precision | 47% (53/112) |
| 信号 F1 | 46% (P=55%, R=40%) |
| 流动性枯竭 | 2 (BSB -80%, GENIUS -99%) |
| 量价背离 | 3 (TAG价跌量增, OL价涨量缩, HYPER价跌量增) |
| 报告耗时 | 20.5s, 16.4KB |

---

## 方法论与实践文档

> 详细文档拆分到 AI_CONTEXT/ 目录

| 文件 | 内容 |
|------|------|
| AI_CONTEXT/methodology.md | 7 模块方法论定义 + 4 个待验证假设(H1-H4) + 实测基线数据 |

### 关键方法论摘要

| 模块 | 核心公式/阈值 | 已知局限 |
|------|-------------|---------|
| 信号回测 | ret = (price_now - entry) / entry × 100, MDD从信号日起算 | 当天信号过滤 |
| Holder 迁移 | retention = old∩new / old × 100 | 低留存≠出货（SKYAI案例） |
| 积分时序 | slope = polyfit(scores, deg=1), σ<0.05归零 | 平序列斜率无意义 |
| 流动性 | vol_change = (24h - 7d_avg) / 7d_avg × 100 | ~60代币volume=0 |
| 信号质量 | P=64%, R=38%, F1=48% (2026-05-06) | 3引擎最佳(67%)，≥4反降(14%) |

### 跟踪验证

- 脚本: meta-verdict/tracking_validator.py
- 基线: 2026-05-06
- 下次验证: 2026-05-10
- 验证 4 个假设: H1(低留存后续) H2(引擎数) H3(积分分桶) H4(2分阈值)


### C9 深度套牢协同持仓 (2026-05-11)

- 信号: `C9` — 深度套牢协同持仓 (ACC, weight=6)
- 条件: VWAP/现价 > 2.5x AND underwater% > 90% AND cost_cv < 0.20 AND holders >= 30
- Verdict: C9 独立 → `COORDINATED_HOLD` (+2分) / C9+C2 → `STEALTH_ACC` (+3分)
- 检测目标: 一致行动人在高位建仓后坚定持有，拉升动机极强
- 首次命中: SQD (vwap/price=3.0x, underwater=97%, CV=0.182, holders=192)
- 精准度: 全量198代币仅命中SQD 1个

### 日志轮转

- 配置: /etc/logrotate.d/ai-sum
- 策略: 每日轮转, 保留 60 天, gzip 压缩, copytruncate

---

## pump_detector v2 — 拉升前兆引擎 (2026-05-10, 双轨与浓度升级 2026-05-19)

### 功能
从 BubbleMap + Gecko + Meta + TokenHistory + Futures 五表计算 9 维 pump_readiness 评分，分级输出告警。

### 9 维评分 (满分128)

| 维度 | 满分 | 数据源 | 说明 |
|------|------|--------|------|
| D1 量缩程度 | 20 | gecko volume_24h vs 7d均量 | DEX量缩+OI暴增→降分(过滤资金搬家假量缩) |
| D2 LP稳定性 | 20 | gecko reserve_usd | LP≥$500K得20分 |
| D3 真实吸筹浓度 | 20 | bubblemap (acc/real_users) | 排除交易所/多重合约噪点后的真实用户吸筹浓度。浓度≥40.0%得20分（35天时序p90水位） |
| D4 Meta持续 | 15 | consec_acc + meta_score | 连续ACC≥25轮且浓度足够得15分 |
| D5 留存率 | 10 | retention_7d | ≥95%得10分 |
| D6 精英均分+大盘门控 | 10 | avg_acc_score + avg_macro_score | 精英主力均分≥80得10分。引入大盘分门控：大盘分<44扣3分；大盘分≥53溢价奖2分（D6上限10分，防溢出） |

### 🚀 2026-05-19 双轨制与真实吸筹浓度升级说明
- **真实用户吸筹浓度**：重构 D3 分母为非交易所、非DEX、非合约的真实独立地址持仓数。精细区间阈值为：`[3%, 8%, 15%, 25%, 40%]`，彻底规避假吸筹噪音引起的评分压制。
- **大盘/精英双轨制**：
  - **精英均分 (avg_acc_score)**：仅统计已被打上 ACC 吸筹标记的核心主力钱包之均分，用于衡量最主力吸筹力量的质量。
  - **大盘分 (avg_macro_score)**：统计所有非 CEX/DEX/合约的独立地址之均分，客观描绘项目的整体大盘持仓基本面质量。
  - **D6门控融合**：D6 分数在继承精英主力均分打分的同时，采用大盘分百分位（低估<44, 强势≥53）对底部分数实施升降级修正，极大强化了评分面对全盘大户共识度时的响应敏锐度。
| D7 OI变化 | 15 | futures oi_change_24h | OI增+价平=暗中建仓, OI<$1M减半 |
| D8 资金费率(FR) | 10 | futures funding_rate | 负FR=空头拥挤→轧空前兆, FR<-0.03%得10分 |
| D9 多空比(LS) | 8 | futures long_short_ratio | L/S<0.6=散户重度做空→强反指看涨 |

### 告警分级 (v2 阈值)
| 级别 | 阈值 | 说明 |
|------|------|------|
| 🔴 IMMINENT | ≥90 | 即将拉升 |
| 🟡 READY_VOL_SHRINK | ≥65 且 vol_ratio<0.3 | 量缩就绪 |
| 🟡 READY | ≥65 | 就绪 |
| 🟢 WATCH | ≥45 | 观察 |

### 报告格式
- IMMINENT 和 READY 使用统一 13 列表格: 代币/pump/D1-D6/D7 OI/D8 FR/D9 LS/吸筹%/vol缩比/OI($)/OI变化/FR/L/S/LP
- 报告头部包含📖列说明(Tips)区块，解释各列含义
- 链上/合约分歧检测：高评分但OI下降的代币单独标注

### 输出
- DB: `pump_alerts` 表（WATCH以上）
- MD: `report/pump/pump_YYYYMMDD_HHMM.md`
- UI: AI-SUM 🚀拉升前兆 Tab

### 调用
- Crontab: meta-verdict 完成后自动调用
- 代码: `meta-verdict/run.py` 中 `pump_detector.run(scan_time)`
- 独立: `python3 meta-verdict/pump_detector.py`

### 数据源
- futures_snapshots 表(select.db), 由 select-coin/futures/binance_futures.py 采集
- Cron: :45 0,6,12,18 (6h/轮, 177代币, 3min完成)

### history_report 模块8
- 新增 futures_analysis(): ACC代币合约验证 + OI变化Top10 + FR极端值


## D10 与 S11 共振引擎升级 (v10.0)
1. **D10 Pool 稳定性评分**:
   - **LP 波动率得分 (Max 4分)**: 统计 gecko_market_data 7天内 reserve_usd 波动范围，波动率 < 3% 得 4 分；< 8% 得 3 分；< 15% 得 2 分；< 25% 得 1 分。
   - **LP 规模得分 (Max 2分)**: 最新 reserve_usd ≥ $500K 得 2 分；≥ $100K 得 1 分。
   - **交易活跃度得分 (Max 2分)**: 24h 交易笔数 ≥ 500 得 2 分；≥ 100 得 1 分。
   - **D10 满分**: LP 波动 + LP 规模 + 活跃度 = 8分，对齐 100 分综合满分权重体系。
2. **S11 静默建仓信号与三维共振 (🤫)**:
   - **三维拟合共振**: LP 极稳 (D10 ≥ 6分) + 合约 OI 缓增 (近3个快照持续增长) + 资金费率 FR 为负 + 链上巨鲸吸筹均分上升！
   - **徽章集成**: 联动 `/api/bubblemap/db/pool-health` 接口，在前端 BubbleMap 列表中自动展示 `🤫` 徽章。
