# AI-SUM — AI 可读项目文档

> **全链路吸筹扫描分析系统**：六引擎架构，基于 BubbleMap 数据库（Top 300 持有者），对所有代币做多维评分与行为模式识别，最终由 meta-verdict 仲裁层汇总输出综合排名。

## 技术栈

| 层 | 技术 |
|---|---|
| 语言 | Python 3.13 |
| 数据库 | SQLite（源库 `select.db`，汇总库 `select-sum.db`） |
| 输出 | 终端 + Markdown 雷达报 |
| 部署 | VPS Linux（`/opt/AI-SUM/`） |

---

## 目录结构

```
AI-SUM/
├── AI_CONTEXT.md                     # ★ 本文件：AI 项目索引
├── .env                              # 环境配置（SRC_DB_PATH/SUM_DB_PATH/阈值）
├── persist_helper.py                 # ★ 三引擎持久化辅助（opus/whale/unified → select-sum.db）
├── patch_engines.py                  # 一次性注入补丁脚本（已执行）
│
├── master-scan/                      # ★ V8.4 生产版（BubbleMap 时序+模式识别）
├── opus-scan/                        # ★ V1.3 双维度诊断（吸筹+出货置信度）
├── unified-scan/                     # ★ V1.2 三维度统一雷达
├── bigcoin/                          # ★ V1.0 庄控预警（7维度置信度）
├── cost-basis-scan/                  # ★ V1.1 成本基础扫描（8信号+SQUEEZE_ACC分层）
├── meta-verdict/                     # ★ NEW 五引擎仲裁层（加权积分+生命周期）
│
├── select-sum.db                     # 汇总分析库（watchlist/cost_basis_snapshots/opus_snapshots/whale_snapshots/meta_snapshots/token_lifecycle）
├── report/
│   ├── v5/                           # master-scan 雷达报
│   ├── opus/                         # opus-scan 报告
│   ├── whale/                        # whale-scan 庄控预警
│   ├── unified/                      # unified-scan 报告
│   ├── cost-basis/                   # cost-basis-scan 报告
│   └── meta/                         # ★ meta-verdict 综合仲裁报告
└── log/
    ├── master.log / opus.log / unified.log
    ├── whale.log / cost-basis.log / meta.log
    └── （logrotate 每周轮转，保留 4 份，单文件 10M 上限）
```

---

## 数据源

| 项 | 说明 |
|---|---|
| VPS 源库 | `/opt/select-coin/data/select.db` |
| 汇总库 | `/opt/AI-SUM/select-sum.db` |
| 主表 | `bubblemap_holders` — 每行 = 地址在某代币某快照的持仓+评分 |
| 辅表 | `token_names` — 代币名称/符号/市值 |
| 评分表 | `token_scores` — 历次扫描的 8 维评分 |
| 市场表 | `gecko_market_data` — GeckoTerminal LP/交易量/FDV 快照 |

---

## 定时调度（Crontab）

```
00:00 master-scan     → /opt/AI-SUM/log/master.log
00:10 opus-scan       → /opt/AI-SUM/log/opus.log
00:20 unified-scan    → /opt/AI-SUM/log/unified.log
00:30 whale-scan      → /opt/AI-SUM/log/whale.log
00:40 cost-basis-scan → /opt/AI-SUM/log/cost-basis.log
00:50 meta-verdict    → /opt/AI-SUM/log/meta.log
12:xx 同上（每天两轮）
```

---

## 六引擎架构总览

| 引擎 | 定位 | 核心输出 | 积分权重 |
|------|------|----------|---------|
| master-scan | 时序模式识别（A/B/C/E） | DIAMOND / RED / YELLOW | +4 / +2 / +1 |
| opus-scan | 双维度诊断（吸筹+出货） | acc_confidence / dist_confidence | acc×0.04 |
| unified-scan | 三维度统一雷达 | 综合信号级别 | +4 / +2 / +1 |
| whale-scan | 庄控预警（7维度） | HIGH / MEDIUM / LOW | +3 / +2 / +1 |
| cost-basis-scan | 成本基础扫描（8信号） | SQUEEZE_ACC_HIGH/MED/LOW / IRON_HOLD 等 | +3 / +2 / +1 |
| **meta-verdict** | **★ 五引擎仲裁汇总** | **综合积分 + 生命周期阶段** | — |

---

## master-scan — V8.4 生产版

### 架构

```
master-scan/
├── run.py                  # 统一入口
├── config.py               # 全局配置（.env 读取）
├── db_loader.py            # SQL 封装 + ATTACH 只读
├── time_series_aligner.py  # SnapshotDiff + V8.2 计算
├── pattern_detector.py     # A/B/C/E 四模式识别
├── report_generator.py     # 雷达报生成
├── engine.py               # 主调度器
└── watchlist_tracker.py    # 追踪生命周期 → 写入 watchlist 表
```

### 四大行为模式

| 模式 | 触发逻辑 | 意义 |
|------|----------|------|
| **A 地址聚合** | 10h 换手率 >3%(黄)/8%(红) + 新吸筹涌入 | 底层筹码重组 |
| **B 新鲸下场** | 新进地址均分≥72 + 纯买入≥80% + 持仓>0.5% | 高质量鲸鱼入场 |
| **C 爆发前静默** | 3/4条件(历史高位+换手<2%+吸筹不减+只买不卖≥70%) | 吸筹完毕静待拉升 |
| **E 钻石绞杀** | 机构控盘率≥85% + DEX真金率≥85% | 极低流通+真金吸筹 |

### 运行

```bash
cd /opt/AI-SUM && python3 master-scan/run.py
```

### 报告输出

`report/v5/radar_YYYYMMDD_HHMM.md`

---

## opus-scan — V1.3 双维度诊断引擎

### 架构

```
opus-scan/
├── run.py                  # 主入口（支持 --offline / --top N）
├── db_loader.py
├── time_series_builder.py  # 全快照时序→斜率/增长率/CEX斜率/阶段判定
├── holder_profiler.py      # Top30 大户行为分析（真鲸/假鲸/卖家/派发者）
├── web_researcher.py       # 联网增强（Gecko Token+Pool API）
├── verdict_engine.py       # 双维度评分（acc 9信号 + dist 12信号）
└── report_generator.py
```

### 持久化（★ 新增）

每次扫描完成后自动调用 `persist_helper.save_opus(results)` 将非 NEUTRAL 结果写入 `select-sum.db → opus_snapshots`，供 meta-verdict 读取。

### 运行

```bash
cd /opt/AI-SUM && python3 opus-scan/run.py --offline   # 定时任务使用
cd /opt/AI-SUM && python3 opus-scan/run.py             # 手动（含 Gecko 联网增强）
```

### 报告输出

`report/opus/opus_YYYYMMDD_HHMM.md`

---

## bigcoin (whale-scan) — V1.0 庄控预警引擎

### 架构

```
bigcoin/
├── run.py
├── concentration_profiler.py   # 集中度 + 锁仓
├── drift_detector.py           # 持仓漂移检测
├── whale_verdict.py            # 7维度裁决引擎（S1-S7）
└── report_generator.py
```

### 持久化（★ 新增）

每次扫描完成后自动调用 `persist_helper.save_whale(results)` 将非 CLEAN 结果写入 `select-sum.db → whale_snapshots`，供 meta-verdict 读取。

### 运行

```bash
cd /opt/AI-SUM && python3 bigcoin/run.py
```

### 报告输出

`report/whale/whale_YYYYMMDD_HHMM.md`

---

## unified-scan — V1.2 三维度统一雷达引擎

### 架构

```
unified-scan/
├── run.py
├── verdict_engine.py           # 三维度评分 + G1/G2/G3 门控
├── report_generator.py
└── analyzers/
    ├── diamond_checker.py      # A1: 钻石绞杀检测
    ├── diff_analyzer.py        # A2/A3: 地址聚合 + 新鲸下场
    ├── cex_flow.py             # A4/D1: CEX 流向
    ├── holder_profiler.py      # D2: 出货者画像
    ├── drift_detector.py       # D3: 被动漂移检测
    ├── concentration.py        # S1: 极端集中度
    └── market_context.py       # S2/S3/S4: 市场结构
```

### 运行

```bash
cd /opt/AI-SUM && python3 unified-scan/run.py --offline   # 定时任务使用
```

### 报告输出

`report/unified/unified_YYYYMMDD_HHMM.md`

---

## cost-basis-scan — V1.1 成本基础扫描

### 架构

```
cost-basis-scan/
├── run.py
├── config.py          # 阈值 + C1-C8 权重
├── db_loader.py       # ATTACH 双库只读
├── cost_profiler.py   # 四成本带分层 + VWAP + CV + C8净流量
├── gravity_tracker.py # 重心漂移 + watchlist 跃迁检测
├── verdict_engine.py  # 8信号评分 + 三层裁决
└── report_generator.py
```

### 8 信号体系

| 信号 | 维度 | 权重 | 含义 |
|------|------|------|------|
| C1 | ACC | 7 (间距>48h降至3) | 水下逆势加仓（被套仍买） |
| C2 | ACC | 6 | 成本聚集（一致行动人，CV<0.15） |
| C3 | ACC | 5 | 暴利区坚定持有（不卖≥50%） |
| C4 | DIST | 7 | 暴利出逃预警 |
| C5 | DIST | 5 | 高位派发（新地址成本≈现价+老鲸减仓） |
| C6 | STRUCT | 5 | 流动性危机（暴利区>40%+LP<$500K） |
| C7 | STRUCT | 4 | 成本倒挂（Top5 VWAP > 尾部 VWAP） |
| **C8** | **DIST** | **4** | **★新 暴利区48h净流出>持仓10%** |

### SQUEEZE_ACC 分层（★ V1.1 新增）

| 层级 | 触发条件 | 含义 |
|------|----------|------|
| SQUEEZE_ACC_LOW | 历史RED + C1 | 单信号，仅水下加仓 |
| SQUEEZE_ACC_MED | 历史RED + C1 + (C2 或 有历史对比) | 中等置信，有佐证 |
| SQUEEZE_ACC_HIGH | 历史RED + C1 + C2 + 有历史对比 | 高置信，多信号共振 |

### 三层裁决

```
第一层: watchlist 跃迁（SQUEEZE_ACC_LOW/MED/HIGH / DEATH_SPIRAL）
第二层: 成本带组合（STEALTH_ACC / IRON_HOLD / LIQUIDITY_CRISIS / PROFIT_EXIT / BAG_PASSING）
第三层: 通用方向（ACCUMULATING / DISTRIBUTING / NEUTRAL）
```

### 快照间距降权

```
C1 信号触发时，若当前快照与上一快照间距 > 48h
→ C1 权重从 7 降至 3，标注 ⚠间距Xh(降权)
原因：间距过大则加仓判定失真
```

### 持久化

结果写入 `select-sum.db → cost_basis_snapshots`（原有机制）。

### 运行

```bash
cd /opt/AI-SUM && python3 cost-basis-scan/run.py
cd /opt/AI-SUM && python3 cost-basis-scan/run.py --symbol SKYAI    # 单币
```

### 报告输出

`report/cost-basis/cb_YYYYMMDD_HHMM.md`

---

## meta-verdict — ★ 五引擎综合仲裁层

### 架构

```
meta-verdict/
├── run.py              # 主入口（仲裁 + 生命周期更新）
├── config.py           # 积分权重配置
├── collector.py        # 从 select-sum.db 收集 5 引擎最新结果
├── arbitrator.py       # 加权仲裁 + 生命周期推断
└── report_generator.py # 综合雷达报（终端 + MD）
```

### 积分权重体系

| 引擎 | 信号 | 积分 |
|------|------|------|
| master-scan | DIAMOND / RED / YELLOW | +4 / +2 / +1 |
| opus-scan | acc_confidence × 0.04 — dist_confidence × 0.04 | 动态 |
| unified-scan | DIAMOND / RED / YELLOW | +4 / +2 / +1 |
| whale-scan | HIGH / MEDIUM / LOW | +3 / +2 / +1 |
| cost-basis-scan | SQUEEZE_ACC_HIGH/STEALTH_ACC(+3) / SQUEEZE_ACC_MED(+2) / SQUEEZE_ACC_LOW/IRON_HOLD(+1) / DEATH_SPIRAL(-5) / LIQUIDITY_CRISIS(-3) | 见左 |

### 裁决阈值

```
综合积分 ≥ 3  → 🎯 ACC（吸筹）
综合积分 ≤ -2 → 💀 DIST（出货）
其余         → NEUTRAL
```

### 生命周期状态机（Phase 3）

| 阶段 | 触发条件 |
|------|----------|
| 🔒 CONTROLLED | master DIAMOND + whale HIGH |
| 🎯 ACCUMULATING | meta_verdict=ACC + master 非 DIAMOND |
| 👀 WATCHLIST | master RED/YELLOW + 综合未达阈值 |
| 💀 DISTRIBUTING | meta_verdict=DIST 或 DEATH_SPIRAL/LIQUIDITY_CRISIS |
| ─ NEUTRAL | 无信号 |

关键跃迁告警：`ACCUMULATING → DISTRIBUTING` 时记录 `⚡ 跃迁` 并写入日志。

### 数据流

```
master-scan → watchlist 表
opus-scan   → opus_snapshots 表      ┐
whale-scan  → whale_snapshots 表     ├ 均通过 persist_helper.py 写入
unified-scan → unified_snapshots 表  ┘  (SUM_DB_PATH from .env)
cost-basis-scan → cost_basis_snapshots 表

meta-verdict → 读取全部 5 表 → 仲裁 → meta_snapshots + token_lifecycle
```

### 运行

```bash
cd /opt/AI-SUM && python3 meta-verdict/run.py
cd /opt/AI-SUM && python3 meta-verdict/run.py --symbol TRADOOR  # 单币
```

### 报告输出

`report/meta/meta_YYYYMMDD_HHMM.md`

---

## select-sum.db 表结构

| 表 | 写入方 | 用途 |
|---|--------|------|
| `watchlist` | master-scan | DIAMOND/RED/YELLOW 历史状态，cost-basis 读取做跃迁联动 |
| `cost_basis_snapshots` | cost-basis-scan | 成本快照历史，meta-verdict 读取 |
| `opus_snapshots` | persist_helper.save_opus | opus 扫描结果，meta-verdict 读取 |
| `whale_snapshots` | persist_helper.save_whale | whale 扫描结果，meta-verdict 读取 |
| `unified_snapshots` | persist_helper.save_unified（待注入） | unified 扫描结果 |
| `meta_snapshots` | meta-verdict | 仲裁结果历史 |
| `token_lifecycle` | meta-verdict | 代币生命周期状态机 |

---

## 版本历史

| 版本 | 日期 | 关键变更 |
|------|------|----------|
| **meta-verdict V1.0** | **2026-04-29** | 五引擎仲裁层上线；token_lifecycle 生命周期状态机；验证：122代币→26吸筹，TRADOOR综合7.5分 |
| **cost-basis-scan V1.1** | **2026-04-29** | SQUEEZE_ACC 三级分层(LOW/MED/HIGH)；C8 暴利区48h净流出信号；C1快照间距>48h降权 |
| **persist_helper** | **2026-04-29** | opus/whale 引擎持久化注入；读取 .env SUM_DB_PATH 避免路径分裂 |
| **Crontab 定时** | **2026-04-29** | 6引擎每天00:xx/12:xx双轮，间隔10分钟；Logrotate每周/10M/4份 |
| cost-basis-scan V1.0 | 2026-04-28 | 8信号成本基础扫描框架上线 |
| **V8.5** | 2026-04-17 | LP/FDV-LP/V-L 三指标升级 |
| **V8.4** | 2026-04-09 | A阈值适配10h周期、C RED强制条件、全局DEX门10% |
| V8.3 | 2026-04-09 | hop2二跳+entity聚簇穿透DEX率 |
| V8.2 | 2026-04-08 | 钻石绞杀区(E模式)、V8.2三指标 |
| V5.0 | 2026-04-03 | 全新多快照时序分析：SnapshotDiff、A/B/C三模式 |

---

## 已知限制与优化方向

| 优先级 | 项目 | 说明 |
|--------|------|------|
| P2 | unified-scan 持久化注入 | `save_unified` 注入已在 `persist_helper.py` 中实现，待 `unified-scan/run.py` 注入 |
| P2 | master-scan 降级逻辑 | 连续 N 次无信号自动从 DIAMOND 降级为 YELLOW |
| P3 | 回测验证 | 对历史 watchlist 代币统计信号后 7d/14d/30d 价格变化 |
| P3 | opus CEX 绝对量级门控 | CEX delta abs > 0.5% 才计入，防小市值虚假信号 |
