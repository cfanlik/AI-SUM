# AI-SUM — AI 可读项目文档

> **全局吸筹扫描分析系统**：基于 BubbleMap 数据库（Top 300 持有者），对所有代币做 8 维综合评分，输出 S/A/B/C/D 五级排名，自动生成 Markdown 分析报告（含跨次对比、复核优先级、结构标签）。

> [!IMPORTANT]
> **AI 更新义务**：修改本项目任何功能后，必须同步更新以下文件并在 `changelog.md` 记录：
> - `AI_CONTEXT.md`（本文件）
> - `SESSION_MEMORY.md`
> - `accumulation_scan_v3_methodology.md`
> - `sql_reference.md`
> - `changelog.md`

## 技术栈

| 层 | 技术 |
|---|---|
| 语言 | Python 3.13 |
| 数据库 | SQLite（外部共享 `select-coin/data/select.db`） |
| 输出 | 终端 + CSV/JSON 导出 + Markdown 报告 |

## 目录结构

```
AI-SUM/
├── AI_CONTEXT.md                     # ★ 本文件：AI 项目索引
├── AI_CONTEXT-codex.md               # Codex 并行评审包（旁路审计说明）
├── SESSION_MEMORY.md                 # 环境信息、最新扫描结果、复核名单
├── accumulation_scan_v3.py           # 主脚本（1187 行）
├── codex-ai-sum.py                   # 并行评审脚本（主看 d1/d4 去重）
├── accumulation_scan_v3_methodology.md  # 评分方法论（7 维度详述）
├── sql_reference.md                  # SQL 查询模板参考
├── changelog.md                      # 版本变更日志
├── exports/                          # CSV/JSON 导出目录
│   └── accumulation_scan_v3_YYYYMMDD_HHMMSS.{csv,json}
└── report/                           # Markdown 报告目录
    ├── last_stats.json               # 上次运行统计（用于跨次对比）
    └── YYYYMMDD_HHMM/               # 按时间戳分目录的报告
        └── report.md
```

## 数据源

| 项 | 说明 |
|---|---|
| 数据库 | `C:\Users\Administrator\.gemini\antigravity\playground\select-coin\data\select.db` |
| 主表 | `bubblemap_holders` — 每行 = 一个地址在某代币某快照的持仓 + 评分 |
| 辅表 | `token_names` — 代币名称/符号/市值缓存（CMC API 按需回填，默认不阻塞扫描） |
| 评分表 | `token_scores` — 每次扫描的 8 维评分时间序列（含 BubbleMap 快照时间范围 + DEX证据观测字段） |
| 数据规模 | ~541MB、472 原始代币 / 469 个入选评分、3 链（eth=266, bsc=188, base=18） |
| 快照深度 | 全部 ≥ 10 次（最多 13 次） |

### bubblemap_holders 关键字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `chain` | TEXT | 链名 (bsc/eth/base) |
| `token_address` | TEXT | 代币合约地址 |
| `snapshot_time` | TEXT | 快照时间 |
| `rank` | INTEGER | **持仓排名** (1-300，按 hold_percentage 排序) |
| `hold_percentage` | REAL | 持仓占比 (%) |
| `buy_amt_usd` / `sell_amt_usd` | REAL | 买入/卖出总额 |
| `acc_score` | REAL | 地址级吸筹评分 (0-100) |
| `is_accumulating` | INTEGER | 是否判定为吸筹 (0/1) |
| `is_cex` / `is_dex` / `is_contract` / `is_supernode` | INTEGER | 地址分类标记 |

> **真实用户** = `is_cex=0 AND is_dex=0 AND is_contract=0 AND is_supernode=0`

## 评分引擎 — V4.0 八维评分

### 两层架构

```
第 1 层 (地址级): BubbleMap 评分引擎 → acc_score + is_accumulating（数据采集阶段完成）
第 2 层 (代币级): 全局扫描引擎 → 8 维聚合 → S/A/B/C/D        （本脚本）
```

### 第 2 层 — 八维综合评分

```python
composite = (d1 * 0.20 +   # 吸筹占比 (基于真实用户，聚类感知)  → 80% = 满分
             d2 * 0.15 +   # 吸筹均分                       → 55-100 映射
             d3 * 0.14 +   # 持仓控制度 (最新快照口径)      → 40% 供应 = 满分
             d4 * 0.12 +   # 卖出抑制 (只买不卖占比，聚类感知) → 66.7% = 满分
             d5 * 0.06 +   # 趋势稳定性 (时间衰减+方向一致性) → v4重写
             d6 * 0.13 +   # 信号质量 (10 类信号加权)       → 大额净流入5分 等
             d7 * 0.10 +   # 集中度 (越分散越高)            → Top1/Top5 评估
             d8 * 0.10)    # 实时活跃度 (Δhold+新地址涌入)    → v4新增
```

### 五级评价

| 等级 | 阈值 | 含义 |
|------|------|------|
| **S** | ≥ 75 | 极强吸筹 |
| **A** | ≥ 60 | 强吸筹 |
| **B** | ≥ 45 | 中等吸筹 |
| **C** | ≥ 30 | 弱吸筹 |
| **D** | < 30 | 微弱信号 |

> 前置过滤: `acc_holders >= 3`（至少 3 个吸筹地址）

### 信号质量权重表

| 信号 | 权重 | 信号 | 权重 |
|------|------|------|------|
| 大额净流入 | 5.0 | 独立地址 | 2.5 |
| 只买不卖 | 4.0 | 大仓 | 2.0 |
| 买>>卖 | 3.0 | 频繁买入 | 2.0 |
| 卖< | 3.0 | 净流入 | 1.5 |
| 巨仓 | 3.0 | 多次买入 | 1.0 |

## 辅助指标（非评分维度）

| 指标 | 说明 |
|------|------|
| `sell_buy_ratio` | 吸筹地址的卖/买比（DEX 交易量比值） |
| `acc_top100_pct` | **持币排名** Top100 中吸筹地址占比（基于 `rank` 字段） |
| `acc_rest_pct` | **持币排名** 101-300 中吸筹地址占比 |
| `top1/3/5_acc_hold` | 最新快照吸筹地址前 N 大的持仓集中度 |
| `latest_only_buy_pct` | 最新快照只买不卖的吸筹地址占比 |
| `cluster_ratio` | 聚类地址占比（entity_id + 特征聚类） |
| `delta_hold` | 最新 vs ~72h 前的吸筹持仓变化 |
| `new_acc_cnt` | 最新快照新增的吸筹地址数 |
| `direct_dex_acc_pct` | 最新快照吸筹地址中，**直接来自 DEX pool swap** 的占比（基于 `dex_ratio>=0.5`） |
| `hop2_dex_acc_pct` | 最新快照吸筹地址中，**二跳 DEX 来源** 的占比（基于 `dex_ratio_hop2>=0.5`） |
| `gmgn_pass_acc_pct` | 最新快照吸筹地址中，**GMGN swap 级验证通过** 的占比（`gmgn_verified>=1`） |
| `gmgn_double_acc_pct` | 最新快照吸筹地址中，**GMGN 双确认** 的占比（`gmgn_verified=2`） |

> **Top100 / 101-300 是持币排名分布**，不是 DEX 交易量。用于判断吸筹来自大户还是中尾部。

## 复核优先级系统

| 优先级 | 触发条件 |
|--------|----------|
| **优先复核** | S/A级 + 分散/良好结构 + 卖/买≤25% + 快照≥3 |
| **谨慎复核** | 含"单一大户主导""高集中度""卖压偏高"标签 |
| **正常复核** | 其余情况 |
| **待确认** | 仅 1 次快照 |

### 结构标签

买盘强（只买>80%）、分散吸筹（acc_h≥100 + top5≤20%）、结构良好、集中度偏高、高集中度、单一大户主导、卖压偏高/较高、结构中性

## 报告生成

运行 `python -X utf8 accumulation_scan_v3.py 4` 自动生成：

1. **终端输出** — 分级代币清单 + 各维度明细
2. **CSV/JSON 导出** → `exports/`
3. **Markdown 报告** → `report/YYYYMMDD_HHMM/report.md`

如需执行远程市值回填，显式使用 `python -X utf8 accumulation_scan_v3.py 4 --backfill-mcap`。
当前 AI-SUM 侧默认固定 CMC 代理为 `socks5://127.0.0.1:18000`，优先于 `select-coin` 配置。

报告包含 7 个章节：总览对比、链分布、**综合评分 TOP 20**、谨慎复核 S 级 TOP 15、关键变化分析、方法论建议、导出文件链接。

## 性能优化要点

| 优化 | 方法 | 效果 |
|------|------|------|
| SQL join | CTE 先聚合再 `(chain, token_address)` 精确 join | 22.6s → 0.19s |
| 批量聚合 | 趋势/信号从 N+1 查询改为批量 + Python 后处理 | 大幅减少查询数 |
| 端到端 | 总耗时（不含可选 CMC 回填） | 24.2s → < 1s |

> ⚠️ **禁止 `LOWER()` join** — 会绕开索引；**JOIN 必须用 `(chain, token_address)` 双键** — 避免跨链串数

## 文档索引

| 文件 | 用途 | 何时需要读 |
|------|------|-----------|
| [accumulation_scan_v3.py](file:///e:/AI-SUM/accumulation_scan_v3.py) | 主脚本（1187 行） | 修改评分/输出/报告逻辑 |
| [codex-ai-sum.py](file:///e:/AI-SUM/codex-ai-sum.py) | 并行评审脚本 | 验证 d1/d4 去重对评分分布的影响 |
| [AI_CONTEXT-codex.md](file:///e:/AI-SUM/AI_CONTEXT-codex.md) | Codex 评审包 | 需要查看审计口径、最新 AB 结论时 |
| [accumulation_scan_v3_methodology.md](file:///e:/AI-SUM/accumulation_scan_v3_methodology.md) | 7 维评分方法论 + 局限性 | 理解评分逻辑、调参、新增维度 |
| [sql_reference.md](file:///e:/AI-SUM/sql_reference.md) | SQL 查询模板 | 编写新查询、排查数据 |
| [SESSION_MEMORY.md](file:///e:/AI-SUM/SESSION_MEMORY.md) | 环境/扫描结果/复核名单 | 了解最新数据状态 |
| [changelog.md](file:///e:/AI-SUM/changelog.md) | 版本变更日志 | 追溯变更历史 |

## 版本历史

| 版本 | 日期 | 关键变更 |
|------|------|----------|
| **v5.1.2** | **2026-04-07** | `accumulation_scan_v3.py` 移除硬编码，全面适配 `.env` 以读取 `SRC_DB_PATH`，修复 Linux 下空库导致的 `no such table` 报错 |
| **v5.1.1** | **2026-04-07** | 修复只读模式下初始化报错问题；新增依赖声明（无第三方包纯净架构） |
| **v5.1** | **2026-04-07** | 支持通过 `.env` 定义 `SUM_DB_PATH` 与 `SRC_DB_PATH`；精简并清理无关项目（nofx等） |
| **v5.0** | **2026-04-03** | **全新 V5 多快照时序分析模块（`ai_sum_v5/`）**：独立 `select-sum.db`、SnapshotDiff 引擎、A/B/C 三模式识别、Watchlist 追踪框架、雷达报生成器；`accumulation_scan_v3.py` 保留不动 |
| v4.2.2 | 2026-03-27 | CMC 回填代理优先固定为 `socks5://127.0.0.1:18000`，避免跟随 `select-coin` 当前代理配置漂移 |
| v4.2.1 | 2026-03-27 | 修正执行工作流：默认跳过 CMC 市值回填，避免本地扫描被远程串行回填阻塞；需显式传 `--backfill-mcap` 开启 |
| v4.2 | 2026-03-27 | 暴露 BubbleMap v6 DEX 证据观测字段（`direct_dex_acc_pct`/`hop2_dex_acc_pct`/`gmgn_pass_acc_pct`/`gmgn_double_acc_pct`），Codex 审计脚本升级到 v4.0 口径 |
| v4.1 | 2026-03-19 | CMC市值回填(集成主脚本)、token_scores评分持久化、BubbleMap时间维度入库 |
| v4.0 | 2026-03-19 | 8维评分(+d8实时活跃度)、d5时间衰减重写、cluster_ratio合并+entity_id去重、S=2/A=139 |
| v3.3 | 2026-03-16 | d3 改为最新快照口径（满分线 40%），报告改为综合分 TOP 20，S=19/A=238 |
| v3.3.1 | 2026-03-18 | `codex-ai-sum.py` 的 `d3` 与主引擎对齐，旁路审计结果更新为 S=12/A=242/B=189/C=26 |
| v3.3.2 | 2026-03-18 | `codex-ai-sum.py` 新增 `--assoc-gt10-bonus` 实验模式；当前数据库下仅产生轻微增分，未改变等级分布 |
| v3.2 | 2026-03-16 | 7 维评分（+集中度 d7）、d5 平方压缩、d6 信号加权、报告格式优化 |
| v3.1 | 2026-03-16 | Top100/101-300 分布、MD 报告生成、跨次对比、DB 路径更正 |
| v3 | 2026-03-13 | 6 维评分（+卖出抑制 d4）、占比基于真实用户、SQL 优化 24s→<1s |
| v2 | 2026-03-13 | 5 维评分、S/A/B/C/D 五级评价 |
| v1 | 2026-03-11 | 初版：2 维排序、3 级星级 |

## 已知局限

| 问题 | 状态 |
|------|------|
| `market_cap_usd` 当前库里仍大面积缺失 | 待处理 — 如需补齐需显式运行 `--backfill-mcap`，不再默认阻塞本地扫描 |
| 无价格历史，无法判断"低位" | 待定 — 需接入 CMC/CoinGecko |
| buy/sell 是 token 级非 USD 级 | 待定 — 需 token_price 归一化 |
| ~~无时间衰减（旧快照等权）~~ | ✅ 已解决 (v4.0 d5 时间衰减) |
| ~~无跨数据源交叉验证~~ | 待定 |
| ~~同构地址簇未去重~~ | ✅ 已解决 (v4.0 cluster_ratio + entity_id) |
| ~~评分未持久化~~ | ✅ 已解决 (v4.1 token_scores 表) |
| ~~静态截面评分，无法感知快照间结构变化~~ | ✅ 已解决 (v5.0 SnapshotDiff + 三模式识别) |

## V5 架构（2026-04-03 新增）

```
AI-SUM/
├── accumulation_scan_v3.py    ← 保留（原 v4.2.2 截面评分）
├── run_v5.py                  ← V5 顶层入口
├── data/select-sum.db         ← V5 独立数据库（不污染 select.db）
├── report/v5/                 ← 雷达报输出目录
└── ai_sum_v5/
    ├── config.py              # 阈值/路径配置
    ├── db_loader.py           # SQL 封装 + ATTACH 只读挂载
    ├── time_series_aligner.py # SnapshotDiff 时序对齐
    ├── pattern_detector.py    # A/B/C 三模式识别
    ├── watchlist_tracker.py   # 追踪框架生命周期管理
    ├── report_generator.py    # 雷达报 Markdown 生成
    └── engine.py              # 主调度器
```

### 运行方式

```bash
python -X utf8 run_v5.py                          # 全库扫描 → 雷达报
python -X utf8 run_v5.py --watchlist              # 查看追踪状态
python -X utf8 run_v5.py --token <addr> <chain>   # 单代币深度分析
python -X utf8 run_v5.py --backtest <addr> <chain># 回溯所有快照
python -X utf8 run_v5.py --dismiss <addr> <chain> # 标记误报
python -X utf8 run_v5.py --pumped <addr> <chain>  # 标记已拉盘
```

### 三大行为模式

| 模式 | 触发逻辑 | 意义 |
|------|----------|------|
| **A 地址聚合** | 12h 换手率 >25%（黄)/40%（红）+ 新吸筹涌入 | 底层筹码重组 |
| **B 新鲸下场** | 新进地址均分≥72 + 纯买入≥80% + 持仓>0.5% | 高质量鲸鱼入场 |
| **C 爆发前静默** | 持仓历史高位 + 换手<5% + 只买不卖≥70% | 吸筹完毕静待拉升 |
