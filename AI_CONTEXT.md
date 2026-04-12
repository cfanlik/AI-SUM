# AI-SUM — AI 可读项目文档

> **全局吸筹扫描分析系统**：基于 BubbleMap 数据库（Top 300 持有者），对所有代币做多维评分与行为模式识别，输出钻石/红色/黄色三级雷达预警。

## 技术栈

| 层 | 技术 |
|---|---|
| 语言 | Python 3.13 |
| 数据库 | SQLite（外部共享 `select-coin/data/select.db`，独立分析库 `select-sum.db`） |
| 输出 | 终端 + Markdown 雷达报 + CSV/JSON 导出 |
| 部署 | 本地 Windows + VPS Linux（`/opt/AI-SUM/`） |

## 目录结构

```
AI-SUM/
├── AI_CONTEXT.md                     # ★ 本文件：AI 项目索引
├── .env                              # 环境配置（SRC_DB_PATH/SUM_DB_PATH/阈值）
├── .gitignore
├── requirements.txt
│
├── accumulation_scan_v3.py           # V3 主脚本（截面评分 S/A/B/C/D）
├── AI-SUM-0408.py                    # V5 顶层入口（路由 python-ai/）
├── run_v5.py                         # V5 备用入口
├── vps_deploy_master.py              # VPS 一键部署+执行+下载报告
│
├── python-ai/                        # V5 原版模块（基线，不含 V8.2）
│   ├── config.py
│   ├── db_loader.py
│   ├── engine.py
│   ├── pattern_detector.py            # A/B/C 三模式
│   ├── report_generator.py
│   ├── time_series_aligner.py
│   └── watchlist_tracker.py
│
├── master-scan/                      # ★ V8.4 生产版（含钻石绞杀区）
│   ├── run.py                         # 统一入口
│   ├── config.py                      # 全局配置（.env 读取）
│   ├── db_loader.py                   # SQL 封装 + ATTACH 只读
│   ├── time_series_aligner.py         # SnapshotDiff + V8.2 计算
│   ├── pattern_detector.py            # A/B/C/E 四模式识别
│   ├── report_generator.py            # 雷达报生成（钻石+红色）
│   ├── engine.py                      # 主调度器
│   └── watchlist_tracker.py           # 追踪生命周期
│
├── opus-scan/                        # ★ V1.1 双维度诊断引擎（吸筹+出货置信度）
│   ├── run.py                         # 统一入口（全库扫描/单币诊断）
│   ├── config.py                      # 阈值配置
│   ├── db_loader.py                   # SQL 封装，ATTACH select.db 只读
│   ├── time_series_builder.py         # 全快照时序→斜率/增长率/CEX斜率/阶段判定
│   ├── holder_profiler.py             # Top30 大户行为分析（真鲸/假鲸/卖家/派发者）
│   ├── web_researcher.py             # 联网增强（Gecko Token+Pool API）
│   ├── verdict_engine.py             # 双维度评分（acc 9信号 + dist 12信号）
│   └── report_generator.py           # 终端双榜 + MD 报告
│
├── data/                             # 独立分析库（.gitignore）
│   └── select-sum.db
├── report/
│   ├── v5/                            # master-scan 雷达报输出
│   └── opus/                          # opus-scan 报告输出
└── exports/                          # CSV/JSON 导出
```

## 数据源

| 项 | 说明 |
|---|---|
| 本地源库 | `C:\Users\Administrator\.gemini\antigravity\playground\select-coin\data\select.db` |
| VPS 源库 | `/opt/select-coin/data/select.db` |
| 主表 | `bubblemap_holders` — 每行 = 地址在某代币某快照的持仓 + 评分 |
| 辅表 | `token_names` — 代币名称/符号/市值 |
| 评分表 | `token_scores` — 历次扫描的 8 维评分 |
| 市场表 | `gecko_market_data` — GeckoTerminal LP/交易量/FDV 快照（链式触发自动写入） |

### bubblemap_holders 关键字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `chain` | TEXT | 链名 (bsc/eth/base) |
| `token_address` | TEXT | 代币合约地址 |
| `snapshot_time` | TEXT | 快照时间 |
| `rank` | INTEGER | 持仓排名 (1-300) |
| `hold_percentage` | REAL | 持仓占比 (%, 100进制: 2.5=2.5%) |
| `acc_score` | REAL | 地址级吸筹评分 (0-100) |
| `is_accumulating` | INTEGER | 是否吸筹 (0/1) |
| `is_cex` / `is_dex` / `is_contract` / `is_supernode` | INTEGER | 地址分类 |
| `dex_ratio` | REAL | 直接 DEX 交互比例 (0-1) |
| `dex_ratio_hop2` | REAL | 二跳 DEX 来源比例 (0-1) |
| `gmgn_verified` | INTEGER | GMGN swap 级验证 (0/1/2) |
| `entity_id` | TEXT | 聚类实体 ID |

## V8.4 Master-Scan 架构

### 四大行为模式

| 模式 | 触发逻辑 | 意义 |
|------|----------|------|
| **A 地址聚合** | 10h 换手率 >3%(黄)/8%(红) + 新吸筹涌入 | 底层筹码重组 |
| **B 新鲸下场** | 新进地址均分≥72 + 纯买入≥80% + 持仓>0.5% | 高质量鲸鱼入场 |
| **C 爆发前静默** | 3/4条件(历史高位+换手<2%+吸筹不减+只买不卖≥70%) + RED需cond_4必须 | 吸筹完毕静待拉升 |
| **E 钻石绞杀** | 机构控盘率≥阈值 + DEX真金率≥阈值（默认双85%） | 极低流通+真金吸筹 |

### 全局信号质量门（V8.4 新增）

```
DEX 真金率 < MIN_SIGNAL_DEX_PCT (默认10%) → 该代币所有 A/B/C 信号被取消
E(DIAMOND) 豁免此过滤
用途：过滤空投/伪信号代币（如 HOLO dex=0%、42 dex=7.5%）
```

### V8.3 核心指标计算

#### 机构控盘率 (institutional_hold_v8)
```
基础设施地址(is_cex/is_contract/is_supernode) 持仓
+ 持仓≥2% 的大户持仓
= 机构控盘率 (100进制)
```

#### 隐庄 (hidden_whale_count)
```
非基础设施 + 持仓≥2% 的地址数量
```

#### DEX真金率 (dex_verified_pct) — V8.3
```
吸筹地址中满足以下任一条件:
  1. dex_ratio ≥ 0.5
  2. dex_ratio_hop2 ≥ 0.5（二跳穿透）
  3. gmgn_verified ≥ 1
  4. 同 entity_id 组内有地址满足上述条件（聚簇穿透）
/ 吸筹地址总数 × 100 (100进制)
```

### 已知限制

| 问题 | 状态 | 优化方向 |
|------|------|----------|
| A→B→C 多跳转账导致 C 的 dex_ratio=0 | ✅ V8.3 已修复 | `dex_ratio_hop2` 二跳穿透 |
| 同主体多钱包未穿透 | ✅ V8.3 已修复 | `entity_id` 聚簇共享 |
| 小地址噪声拉低 DEX率 | 待定 | 持仓加权替代数量占比 |
| 历史中位数被早期低值拉低 | ✅ V8.3 已修复 | 近20快照滑动窗口 |
| 无价格维度 | 待定 | 引入价格-持仓背离检测 |

### 报告输出格式

仅展示两个核心区块：
1. **💎 钻石绞杀区** — 双阈值严格通过（默认双85%，可通过 .env 调整）
2. **🔴 红色预警** — A/B/C 模式触发

统一8列：`代币 | 链 | 信号级别 | 触发模式 | 机构控盘率 | 隐庄 | DEX真金率 | 吸筹数`

不展示：🟡 黄色关注、📋 Watchlist（避免报告冗长）

### 运行方式

```bash
# 本地执行
python master-scan/run.py

# VPS 一键部署+执行+下载报告
python vps_deploy_master.py
```

### 环境配置 (.env)

```env
SRC_DB_PATH=C:\Users\Administrator\.gemini\antigravity\playground\select-coin\data\select.db
SUM_DB_PATH=E:\AI-SUM\data\select-sum.db
DIAMOND_INST_THRESHOLD=85.0
DIAMOND_DEX_THRESHOLD=85.0
HIDDEN_WHALE_HOLD_THRESHOLD=2.0
MIN_SIGNAL_DEX_PCT=10.0
```

## V3 截面评分（保留）

### 八维综合评分

```python
composite = (d1 * 0.20 +   # 吸筹占比       → 80% = 满分
             d2 * 0.15 +   # 吸筹均分       → 55-100 映射
             d3 * 0.14 +   # 持仓控制度     → 40% = 满分
             d4 * 0.12 +   # 卖出抑制       → 66.7% = 满分
             d5 * 0.06 +   # 趋势稳定性
             d6 * 0.13 +   # 信号质量
             d7 * 0.10 +   # 集中度
             d8 * 0.10)    # 实时活跃度
```

### 五级评价

| 等级 | 阈值 | 含义 |
|------|------|------|
| **S** | ≥ 75 | 极强吸筹 |
| **A** | ≥ 60 | 强吸筹 |
| **B** | ≥ 45 | 中等吸筹 |
| **C** | ≥ 30 | 弱吸筹 |
| **D** | < 30 | 微弱信号 |

## 版本历史

| 版本 | 日期 | 关键变更 |
|------|------|----------|
| **V4.4** | **2026-04-10** | v7 过滤门：LP<$50K/V/L>10 否决 + FDV/LP>500 降级×0.8 + 48h cutoff 改 max(tx.date) + D5 新代币 surge=999 封顐50 |
| V4.3 | 2026-04-10 | Gecko 市场结构集成：classify_review 新增 FDV/LP、V/L、洗盘标签 + MD 报告第 8 节「市场结构异常」（市值虚高表+疑似洗盘表）+ 谨慎复核降级规则 |
| **V8.4** | **2026-04-09** | A阈值适配10h周期(3%/8%)、C RED=3/4+cond4强制、cond2收紧至2%、全局DEX门10%(防空投)、ARIA回归RED |
| V8.3 | 2026-04-09 | hop2二跳+entity聚簇穿透DEX率、近20滑动窗口中位数、阈值/隐庄配置化(.env)、双85%默认阈值 |
| V8.2 | 2026-04-08 | master-scan 生产版：钻石绞杀区(模式E)、V8.2 三指标(机构控盘/隐庄/DEX真金率)、统一8列报告格式 |
| **V5.1.2** | 2026-04-07 | `accumulation_scan_v3.py` 适配 `.env`，修复 Linux 兼容 |
| **V5.0** | 2026-04-03 | 全新多快照时序分析：SnapshotDiff、A/B/C 三模式、Watchlist 追踪 |
| v4.2.2 | 2026-03-27 | CMC 代理固定 `socks5://127.0.0.1:18000` |
| v4.0 | 2026-03-19 | 8维评分(+d8)、d5时间衰减、cluster_ratio |

## opus-scan V1.1 双维度诊断引擎

### 架构概述

基于全部历史快照时序分析，对每个代币输出 **吸筹置信度** 和 **出货置信度** 双评分。

### 吸筹信号体系 (acc, 9信号, 总权重 21)

| # | 信号名 | 权重 | 触发条件 |
|---|--------|------|----------|
| 1 | acc_trend_up | 3 | acc_cnt 斜率 > 0 |
| 2 | acc_hold_growing | 3 | acc_hold 增长 > 阈值 |
| 3 | dex_rate_high | 3 | DEX真金率 > 50% |
| 4 | strong_buyers | 2 | 强买入者 ≥ 3 |
| 5 | no_major_seller | 2 | 出货者持仓 < 5% |
| 6 | net_inflow_positive | 2 | 吸筹者净流入全正 |
| 7 | **cex_outflow** | **3** | **CEX占比下降>3% 且 斜率<-0.2（交易所流出=持有意图）** |
| 8 | price_not_pumped | 1 | 24h价格未暴涨 |
| 9 | buy_sell_person_ratio | 2 | 买/卖人数比 ≥ 0.8 |

### 出货信号体系 (dist, 12信号, 总权重 24)

| # | 信号名 | 权重 | 触发条件 |
|---|--------|------|----------|
| 1 | cex_declining | 3 | CEX持仓下降 > 阈值 |
| 2 | major_seller | 3 | 出货者持仓 ≥ 1% |
| 3 | fake_whales | 3 | 假鲸鱼 ≥ 2 |
| 4 | distribution_48h | 2 | 48h派发者 ≥ 3 |
| 5 | acc_hold_stagnant | 2 | acc增长<5% 且 斜率<0.1 |
| 6 | seller_hold_heavy | 2 | 出货者持仓 ≥ 5% |
| 7 | price_rising_cover | 1 | 价格上涨>10% 掩护出货 |
| 8 | acc_insufficient | 1 | 吸筹者持仓 < 5% |
| 9 | volume_declining | 2 | 24h量缩 |
| 10 | lp_thin | 1 | LP不足 |
| 11 | price_7d_drop | 1 | 7d价格下跌 |
| 12 | **cex_inflow** | **2** | **CEX占比上升>3% 且 斜率>0.2（代币流入交易所=出货前兆）** |

### V1.1 CEX 流向信号设计原理

```
CEX 地址持仓占比持续下降
  → 代币从交易所提到个人钱包
  → 持有者意图长期持有（否则留交易所更方便卖）
  → = 吸筹行为的最强结构性证据

CEX 地址持仓占比持续上升
  → 代币被充入交易所
  → 持有者准备卖出
  → = 出货前兆信号
```

实测验证（AGT/IRYS/GWEI 三代币 45+ 快照）：
- AGT: CEX 13.5%→09.0% (Δ-4.5%), acc 7.6%→14.4% — 完美反向相关
- IRYS: CEX ±0.6%, 信号不触发 — 零影响
- GWEI: CEX 14.2%→19.3% (Δ+5.1%), 价格冲高回落 — 出货前兆确认

### 运行方式

```bash
# VPS 全库扫描
cd /opt/AI-SUM && python3 opus-scan/run.py

# 单币诊断
cd /opt/AI-SUM && python3 opus-scan/run.py --symbol AGT
```
